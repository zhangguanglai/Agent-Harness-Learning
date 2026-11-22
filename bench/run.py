"""基准运行器：跑任务集 → 出指标 → 供 CI 做门禁。

用法：
    python bench/run.py                       # 全部任务
    python bench/run.py --tasks fix-typo      # 指定任务
    python bench/run.py --json bench/last.json --min-pass-rate 0.8

退出码：0 = 达到通过率门槛；1 = 未达到（可直接用于 CI）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.tasks import BenchTask, get_tasks  # noqa: E402
from runtime import ApprovalBroker, InMemoryStore, Runtime, RunRequest, summarize_events  # noqa: E402
from runtime.policy import capabilities_for  # noqa: E402

WORK_DIR = ROOT / "bench" / "_work"


def prepare_workspace(task: BenchTask) -> Path:
    workspace = WORK_DIR / task.id
    shutil.rmtree(workspace, ignore_errors=True)
    workspace.mkdir(parents=True, exist_ok=True)
    for rel, content in task.fixtures.items():
        path = workspace / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return workspace


def run_one(runtime: Runtime, task: BenchTask) -> dict:
    workspace = prepare_workspace(task)
    request = RunRequest(
        task=task.goal,
        session_id=f"bench-{task.id}-{int(time.time())}",
        tenant="bench",
        user="bench-runner",
        roles=task.roles,
        workspace=workspace,
        mode=task.mode,
        allow=task.allow,
        deny=task.deny,
        llm=task.make_llm(),
    )
    started = time.perf_counter()
    handle = runtime.submit(request)
    finished = handle.wait(task.timeout_s)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    if not finished:
        handle.cancel("bench_timeout")

    events = handle.events()
    summary = summarize_events(events)
    finished_event = next((e for e in events if e.kind in {"run_finished", "run_cancelled", "run_failed"}), None)
    stopped = str((finished_event.data.get("stopped") if finished_event else "") or "")
    passed, reason = (False, "运行未结束（超时）") if not finished else task.accept(handle, workspace)
    return {
        "task": task.id,
        "title": task.title,
        "status": handle.status.value,
        "stopped": stopped,
        "passed": bool(passed),
        "reason": reason,
        "turns": int((finished_event.data.get("turns") if finished_event else 0) or 0),
        "tool_calls": summary["tool_calls"],
        "denials": summary["denials"],
        "input_tokens": summary["input_tokens"],
        "output_tokens": summary["output_tokens"],
        "duration_ms": elapsed_ms,
        "event_count": len(events),
        "workspace": str(workspace),
    }


def print_table(records: list[dict]) -> None:
    header = f"{'task':<22}{'status':<10}{'pass':<6}{'turns':>6}{'tools':>7}{'deny':>6}{'tok_in':>8}{'tok_out':>9}{'ms':>9}"
    print(header)
    print("-" * len(header))
    for record in records:
        mark = "✅" if record["passed"] else "❌"
        print(
            f"{record['task']:<22}{record['status']:<10}{mark:<6}{record['turns']:>6}{record['tool_calls']:>7}"
            f"{record['denials']:>6}{record['input_tokens']:>8}{record['output_tokens']:>9}{record['duration_ms']:>9.0f}"
        )
        if not record["passed"]:
            print(f"{'':<22}└─ {record['reason']}")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    parser = argparse.ArgumentParser(description="mini-harness 基准任务集")
    parser.add_argument("--tasks", nargs="*", default=None, help="只跑指定任务 id")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--json", default=None, help="把结果写入 JSON 文件")
    parser.add_argument("--min-pass-rate", type=float, default=0.0, help="低于该通过率则退出码 1")
    parser.add_argument("--keep", action="store_true", help="保留工作区目录")
    parser.add_argument("--list", action="store_true", help="只列出任务")
    args = parser.parse_args(argv)

    tasks = get_tasks(args.tasks)
    if args.list:
        for task in tasks:
            caps = capabilities_for(task.roles)
            print(f"{task.id:<22}{task.mode.value:<16}{task.title}  (工具: {len(caps) if caps else '全部'})")
        return 0
    if not tasks:
        print("没有匹配的任务。")
        return 1

    runtime = Runtime(InMemoryStore(), workers=args.workers, broker=ApprovalBroker(auto_approve=True))
    records: list[dict] = []
    try:
        for task in tasks:
            record = run_one(runtime, task)
            records.append(record)
            print(f"[{len(records)}/{len(tasks)}] {task.id}: {'PASS' if record['passed'] else 'FAIL'}")
    finally:
        runtime.close()

    print()
    print_table(records)
    passed = sum(1 for r in records if r["passed"])
    rate = passed / len(records)
    total_in = sum(r["input_tokens"] for r in records)
    total_out = sum(r["output_tokens"] for r in records)
    avg_turns = sum(r["turns"] for r in records) / max(1, len(records))
    print(
        f"\n通过率 {passed}/{len(records)} = {rate:.0%} · 平均轮次 {avg_turns:.1f} · "
        f"token 输入 {total_in} / 输出 {total_out}"
    )

    if args.json:
        payload = {"pass_rate": rate, "records": records, "generated_at": time.time()}
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"结果已写入 {args.json}")

    if not args.keep:
        shutil.rmtree(WORK_DIR, ignore_errors=True)

    return 0 if rate >= args.min_pass_rate else 1


if __name__ == "__main__":
    sys.exit(main())
