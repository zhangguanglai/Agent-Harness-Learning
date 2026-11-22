"""命令行入口：demo / run / repl 三个子命令。

`demo` 不联网、不需要 API Key，用 DemoLLM 走完一次真实的多轮工具循环，
适合第一次跑通后立刻建立「它到底在干什么」的直觉。
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path
from typing import Sequence

from .hooks import Hooks, secret_guard
from .llm import DemoLLM, OpenAICompatLLM, ScriptedLLM, ScriptedLLM as _Scripted
from .loop import Agent, AgentConfig, RunResult
from .policy import Mode
from .trace import Tracer
from .util import ensure_dir, write_text


# --------------------------------------------------------------------------- #
# 输出渲染
# --------------------------------------------------------------------------- #
def render(event: dict) -> None:
    kind = event.get("type")
    if kind == "turn":
        print(f"\n── 第 {event['turn']} 轮 " + "─" * 40)
    elif kind == "assistant":
        if event.get("text"):
            print(f"🤖 {event['text']}")
        for summary in event.get("tool_calls") or []:
            print(f"🔧 调用 {summary}")
    elif kind == "permission":
        decision = event.get("decision", "")
        if decision.startswith("DENY") or decision.startswith("ASK"):
            print(f"🛡️  {decision}")
    elif kind == "tool_result":
        mark = "✅" if event.get("ok") else ("⛔" if event.get("denied") else "❌")
        first = (event.get("output") or "").strip().splitlines()
        head = first[0][:120] if first else ""
        print(f"{mark} {event['tool']} → {head}")
    elif kind == "compaction":
        report = event.get("report", {})
        print(f"🗜️  上下文压缩：{report.get('before_tokens')} → {report.get('after_tokens')} tokens")


def make_approver(auto: bool):
    if auto:
        return lambda call, decision: True

    def ask(call, decision) -> bool:
        print(f"\n⚠️  需要确认：{call.summary()}\n    理由：{decision.reason}")
        try:
            answer = input("    允许执行？[y/N] ").strip().lower()
        except EOFError:
            return False
        return answer in {"y", "yes"}

    return ask


# --------------------------------------------------------------------------- #
# Agent 组装
# --------------------------------------------------------------------------- #
def build_agent(args: argparse.Namespace) -> Agent:
    workspace = Path(args.workspace).resolve()
    ensure_dir(workspace)
    memory_home = Path(args.memory_home).resolve() if args.memory_home else workspace / ".mini" / "memory"
    skills_root = Path(args.skills_root).resolve() if args.skills_root else workspace / "skills"
    trace_path = Path(args.trace).resolve() if args.trace else workspace / ".mini" / "traces" / f"{int(time.time())}.jsonl"

    tracer = Tracer(path=trace_path, echo=args.verbose)
    config = AgentConfig(
        workspace=workspace,
        mode=Mode(args.mode),
        allow=tuple(args.allow or ()),
        deny=tuple(args.deny or ()),
        ask=tuple(args.ask or ()),
        max_turns=args.max_turns,
        token_budget=args.token_budget,
        max_context_tokens=args.context_tokens,
        compact_at=args.compact_at,
        memory_home=memory_home,
        skills_root=skills_root if skills_root.is_dir() else None,
        system_extra=args.system_extra or "",
        auto_deny_unapproved=args.deny_unapproved,
    )

    provider = args.provider
    if provider == "demo":
        llm = DemoLLM(target_file=getattr(args, "demo_target", "NOTES.md"))
    elif provider == "openai":
        llm = OpenAICompatLLM(model=args.model, base_url=args.base_url, api_key=args.api_key)
    else:
        raise SystemExit(f"未知 provider: {provider}")

    hooks = Hooks()
    if args.secret_guard:
        hooks.on_before(secret_guard)
    return Agent(llm, config, tracer=tracer, hooks=hooks, approver=make_approver(args.yes), on_event=render)


def run_task(agent: Agent, task: str, history: list[dict] | None = None) -> RunResult:
    print(f"🎯 任务：{task}")
    result = agent.run(task, history=history)
    agent.tracer.print_summary()
    print(f"\n🏁 停止原因：{result.stopped} · 轮次：{result.turns}")
    return result


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #
def cmd_demo(args: argparse.Namespace) -> int:
    root = Path(args.workspace).resolve() / ".mini-demo"
    if root.exists():
        shutil.rmtree(root)
    workspace = ensure_dir(root / "workspace")
    write_text(
        workspace / "README.md",
        "# 示例项目\n\n"
        "这个仓库用来验证 mini-harness 的多轮工具循环。\n\n"
        "- 入口：`mini_harness/loop.py`\n"
        "- 实验：`labs/`\n"
        "- 验收：`python labs/verify.py`\n",
    )
    write_text(workspace / "src" / "app.py", "def greet(name: str) -> str:\n    return f'hello {name}'\n")

    args.workspace = str(workspace)
    args.memory_home = str(root / "memory")
    args.skills_root = None
    args.yes = True  # 演示用：写操作自动批准，且工作区是一次性目录
    agent = build_agent(args)
    print("📄 系统提示（节选）\n" + "\n".join(agent._build_system("").splitlines()[:14]) + "\n…")
    result = run_task(agent, "先看工作区结构，读 README.md，然后写一份 NOTES.md 记录你做了什么。")
    notes = workspace / "NOTES.md"
    if notes.exists():
        print("\n📝 生成的 NOTES.md：\n" + notes.read_text(encoding="utf-8"))
    print(f"\n轨迹：{agent.tracer.path}")
    return 0 if result.ok else 1


def cmd_run(args: argparse.Namespace) -> int:
    agent = build_agent(args)
    if args.print_system:
        print(agent._build_system(""))
        return 0
    result = run_task(agent, args.task)
    return 0 if result.ok else 1


def cmd_repl(args: argparse.Namespace) -> int:
    agent = build_agent(args)
    history: list[dict] = []
    print("mini-harness REPL —— /exit 退出，/clear 清空历史，/mode <name> 切换权限模式。")
    while True:
        try:
            line = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in {"/exit", "/quit"}:
            return 0
        if line == "/clear":
            history.clear()
            print("历史已清空。")
            continue
        if line.startswith("/mode "):
            try:
                agent.policy.mode = Mode(line.split(maxsplit=1)[1])
                print(f"权限模式 → {agent.policy.mode.value}")
            except ValueError:
                print("可选：default / acceptEdits / plan / dontAsk / bypassPermissions")
            continue
        result = agent.run(line, history=history)
        history = [m for m in result.messages[1:] if m.get("role") != "system"]
        print(f"\n（{result.stopped} · {result.turns} 轮 · token {result.usage.total}）")


# --------------------------------------------------------------------------- #
# 参数
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", default=".", help="工作区（沙箱根），默认当前目录")
    common.add_argument("--provider", choices=["demo", "openai"], default="demo")
    common.add_argument("--model", default="gpt-4o-mini")
    common.add_argument("--base-url", default=None, help="OpenAI 兼容服务地址，如 https://api.deepseek.com/v1")
    common.add_argument("--api-key", default=None, help="也可用环境变量 OPENAI_API_KEY")
    common.add_argument("--mode", default="default", choices=[m.value for m in Mode])
    common.add_argument("--allow", action="append", default=[], metavar="RULE", help="如 'Bash(echo *)'，可重复")
    common.add_argument("--deny", action="append", default=[], metavar="RULE")
    common.add_argument("--ask", action="append", default=[], metavar="RULE")
    common.add_argument("--max-turns", type=int, default=16)
    common.add_argument("--token-budget", type=int, default=120_000)
    common.add_argument("--context-tokens", type=int, default=8_000)
    common.add_argument("--compact-at", type=float, default=0.6)
    common.add_argument("--memory-home", default=None)
    common.add_argument("--skills-root", default=None)
    common.add_argument("--trace", default=None)
    common.add_argument("--system-extra", default="")
    common.add_argument("--yes", action="store_true", help="自动批准所有 ask")
    common.add_argument("--deny-unapproved", action="store_true", help="无审批人时直接拒绝 ask（CI 用法）")
    common.add_argument("--secret-guard", action="store_true", help="启用密钥写入拦截 Hook")
    common.add_argument("--verbose", action="store_true", help="打印轨迹事件")

    parser = argparse.ArgumentParser(prog="mini_harness", description="学习用的 Agent Harness 最小实现")
    sub = parser.add_subparsers(dest="command", required=True)

    p_demo = sub.add_parser("demo", parents=[common], help="离线演示一次完整多轮循环")
    p_demo.add_argument("--demo-target", default="NOTES.md")
    p_demo.set_defaults(func=cmd_demo)

    p_run = sub.add_parser("run", parents=[common], help="执行一个任务")
    p_run.add_argument("task")
    p_run.add_argument("--print-system", action="store_true", help="只打印系统提示后退出")
    p_run.set_defaults(func=cmd_run)

    p_repl = sub.add_parser("repl", parents=[common], help="交互式会话")
    p_repl.set_defaults(func=cmd_repl)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    # Windows 终端默认 GBK，直接打印 emoji 会 UnicodeEncodeError；统一切到 UTF-8。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\n已中断。")
        return 130
