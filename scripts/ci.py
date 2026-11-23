"""本地 CI：与 .github/workflows/ci.yml 的三个 job 对齐。

用途：GitHub Actions 因账单/额度不可用时，团队仍能在本地或 pre-push 阶段跑同一套门禁。

    python scripts/ci.py                # labs + worker + 容器冒烟
    python scripts/ci.py --fast         # 跳过容器（约 2 分钟）
    python scripts/ci.py --container-only

退出码：0 全绿；1 有失败。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "spike" / "dsh-worker"


def run(name: str, args: list[str], *, cwd: Path = ROOT, timeout: float = 1800) -> bool:
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError:
        print(f"❌ {name}：找不到命令 {args[0]}")
        return False
    except subprocess.TimeoutExpired:
        print(f"❌ {name}：超时（{timeout:.0f}s）")
        return False
    elapsed = time.perf_counter() - started
    ok = proc.returncode == 0
    print(f"{'✅' if ok else '❌'} {name}（{elapsed:.0f}s，exit={proc.returncode}）")
    if not ok:
        tail = (proc.stdout + "\n" + proc.stderr).strip().splitlines()[-25:]
        for line in tail:
            print(f"   {line}")
    return ok


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    parser = argparse.ArgumentParser(description="本地 CI（与 GitHub Actions 对齐）")
    parser.add_argument("--fast", action="store_true", help="跳过容器 job")
    parser.add_argument("--container-only", action="store_true", help="只跑容器 job")
    args = parser.parse_args(argv)

    node = shutil.which("node")
    if not node:
        print("❌ 找不到 node（worker job 需要 Node ≥ 20）")
        return 1

    results: dict[str, bool] = {}
    started = time.perf_counter()

    if not args.container_only:
        results["labs"] = run("labs + bench（16 个实验）", [sys.executable, "labs/verify.py"], timeout=1200)
        results["worker:loopback"] = run(
            "worker 协议测试（loopback）", [node, "test/worker.test.js"], cwd=WORKER, timeout=300
        )
        results["worker:spawn"] = run(
            "worker 子进程测试（spawn）", [node, "test/spawn.test.js"], cwd=WORKER, timeout=300
        )

    if not args.fast:
        results["container"] = run(
            "容器镜像（无模型冒烟）",
            [sys.executable, "deploy/s0-container/verify.py", "--build", "--no-model"],
            timeout=1800,
        )

    print()
    for name, ok in results.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    total = time.perf_counter() - started
    failed = [name for name, ok in results.items() if not ok]
    print(f"\n合计 {len(results)} 项，耗时 {total:.0f}s，失败 {len(failed)} 项")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
