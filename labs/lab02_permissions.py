"""实验 2：权限三层 —— 规则、模式、沙箱。

要看到的结论：
- 沙箱先于规则：越界路径不论什么模式都拒绝；
- 灾难性命令是硬拦截，bypassPermissions 也不放行；
- 规则求值顺序 deny → ask → allow；
- 模式决定「没写规则时」的默认行为；
- 被拒绝的结果会回到对话里，模型可以改道；连续被拒则循环主动停止。

运行：python labs/lab02_permissions.py
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import shutil
from pathlib import Path

from mini_harness import Agent, AgentConfig, Mode, ScriptedLLM, ToolContext, call, reply
from mini_harness.policy import PermissionPolicy
from mini_harness.types import ToolCall

TMP = _bootstrap.TMP_ROOT / "lab02"


def prepare() -> Path:
    shutil.rmtree(TMP, ignore_errors=True)
    ws = TMP / "ws"
    ws.mkdir(parents=True)
    (ws / "README.md").write_text("# demo\n", encoding="utf-8")
    return ws


def ctx_for(ws: Path) -> ToolContext:
    return ToolContext(workspace=ws)


def run() -> None:
    ws = prepare()

    # ---- 1. 沙箱：越界路径 ------------------------------------------------- #
    esc = Agent(
        ScriptedLLM([call("write_file", path="../escaped.txt", content="pwn"), reply("被拦住了，我停下来说明情况。")]),
        AgentConfig(workspace=ws, mode=Mode.BYPASS),
    )
    r = esc.run("把文件写到工作区外面")
    assert any("路径越界" in d for d in r.denials), r.denials
    assert not (ws.parent / "escaped.txt").exists(), "沙箱必须真的挡住写入"
    assert r.stopped == "final", r.stopped
    print("✓ 沙箱：../escaped.txt 被拒绝，文件没有落地，模型收到拒绝原因后改口")

    # ---- 2. 灾难性命令：bypass 也拦 --------------------------------------- #
    policy_bypass = PermissionPolicy(mode=Mode.BYPASS, sandbox_root=ws)
    d = policy_bypass.decide(ToolCall(name="bash", arguments={"command": "rm -rf /"}), ctx_for(ws))
    assert d.action == "deny" and "灾难性" in d.reason, d
    print(f"✓ 黑名单：{d}")

    # ---- 3. 规则顺序：deny 压过 allow ------------------------------------- #
    policy = PermissionPolicy(
        mode=Mode.DEFAULT,
        allow=["Bash(git status *)", "Bash(echo *)"],
        deny=["Bash(git push *)"],
        sandbox_root=ws,
    )
    ok = policy.decide(ToolCall(name="bash", arguments={"command": "git status --short"}), ctx_for(ws))
    blocked = policy.decide(ToolCall(name="bash", arguments={"command": "git push origin main"}), ctx_for(ws))
    assert ok.action == "allow" and blocked.action == "deny", (ok, blocked)
    print("✓ 规则：git status 放行，git push 被 deny 规则拦下")

    # ---- 4. plan 模式：只读 ------------------------------------------------- #
    plan = Agent(
        ScriptedLLM([call("write_file", path="NOTE.md", content="x"), reply("plan 模式下我只规划，不动手。")]),
        AgentConfig(workspace=ws, mode=Mode.PLAN),
    )
    rp = plan.run("在 plan 模式下写作")
    assert any("plan 模式" in d for d in rp.denials), rp.denials
    # plan 模式下模型连写工具的 schema 都看不到
    assert "write_file" not in [s["function"]["name"] for s in plan._schemas()]
    assert not (ws / "NOTE.md").exists()
    print("✓ plan 模式：写工具既不在 schema 里，也会在执行层被拒")

    # ---- 5. ask：审批人决定 ------------------------------------------------ #
    asked = Agent(
        ScriptedLLM([call("write_file", path="a.txt", content="hello"), reply("没获批，我不写了。")]),
        AgentConfig(workspace=ws, mode=Mode.DEFAULT),
        approver=lambda c, d: False,
    )
    ra = asked.run("写个文件")
    assert any("人工拒绝" in d for d in ra.denials), ra.denials
    assert not (ws / "a.txt").exists()

    approved = Agent(
        ScriptedLLM([call("write_file", path="a.txt", content="hello"), reply("已写入 a.txt")]),
        AgentConfig(workspace=ws, mode=Mode.DEFAULT),
        approver=lambda c, d: True,
    )
    rb = approved.run("写个文件")
    assert rb.stopped == "final" and (ws / "a.txt").read_text(encoding="utf-8") == "hello"
    print("✓ ask：审批人拒绝则不写盘，批准才真的执行")

    # ---- 6. dontAsk：CI 默认拒绝 ------------------------------------------ #
    ci = Agent(
        ScriptedLLM([call("bash", command="echo hi"), reply("...")]),
        AgentConfig(workspace=ws, mode=Mode.DONT_ASK),
    )
    rc = ci.run("在 CI 模式下执行命令")
    assert any("dontAsk" in d for d in rc.denials), rc.denials
    print("✓ dontAsk：未预授权的执行类工具直接拒绝（适合 CI）")

    # ---- 7. 连续被拒 → 循环主动停止 --------------------------------------- #
    stubborn = Agent(
        ScriptedLLM([call("write_file", path="b.txt", content="x") for _ in range(4)]),
        AgentConfig(workspace=ws, mode=Mode.DEFAULT),
        approver=lambda c, d: False,
    )
    rs = stubborn.run("反复尝试写入")
    assert rs.stopped == "denied", rs.stopped
    assert rs.turns == 2, rs.turns
    print("✓ 连续两轮全部被拒 → stopped=denied，避免与权限层死循环")


if __name__ == "__main__":
    run()
    print("\nLAB02 PASS")
