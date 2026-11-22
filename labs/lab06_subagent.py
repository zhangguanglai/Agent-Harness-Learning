"""实验 6：子代理 —— 上下文隔离与结论回传。

要看到的结论：
- 子代理在自己的上下文里跑完整循环，父会话只收到结论摘要；
- 子代理继承父会话的权限模式，不能自行提权；
- 深度上限 1，避免代理自我复制；
- 轨迹里能看到 subagent_start / subagent_end。

运行：python labs/lab06_subagent.py
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import shutil
from pathlib import Path

from mini_harness import Agent, AgentConfig, Mode, ScriptedLLM, call, reply

TMP = _bootstrap.TMP_ROOT / "lab06"


def prepare() -> Path:
    shutil.rmtree(TMP, ignore_errors=True)
    ws = TMP / "ws"
    (ws / "src").mkdir(parents=True)
    for i in range(3):
        (ws / "src" / f"m{i}.py").write_text(f"VALUE_{i} = {i}\n", encoding="utf-8")
    (ws / "README.md").write_text("# demo\n", encoding="utf-8")
    return ws


def run() -> None:
    ws = prepare()

    # 剧本顺序是全局的：父 delegate → 子 list_dir → 子收尾 → 父收尾
    llm = ScriptedLLM(
        [
            call("delegate", goal="统计 src/ 下有几个 .py 文件，并说出文件名"),
            call("list_dir", path="src"),
            reply("src/ 下有 3 个 Python 文件：m0.py、m1.py、m2.py。"),
            reply("已派子代理完成统计，父会话收到结论。"),
        ]
    )
    agent = Agent(llm, AgentConfig(workspace=ws, mode=Mode.DEFAULT, max_turns=6))
    result = agent.run("统计 src 下的 Python 文件数量")

    assert result.stopped == "final", result.stopped
    assert result.turns == 2, result.turns

    tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
    assert [m["name"] for m in tool_msgs] == ["delegate"], "父上下文里只应有 delegate 结果"

    summary = tool_msgs[0]["content"]
    assert "[子代理结论" in summary, summary[:120]
    assert "m0.py" in summary, "结论里应带关键事实"
    assert not any(m.get("name") == "list_dir" for m in result.messages), "子代理的中间步骤不应进入父上下文"
    print("✓ 隔离：父上下文只有一行 delegate 结论，没有子代理的 list_dir 原始输出")

    kinds = [e["kind"] for e in agent.tracer.events]
    assert "subagent_start" in kinds and "subagent_end" in kinds
    end = next(e for e in agent.tracer.events if e["kind"] == "subagent_end")
    assert end["turns"] >= 2 and end["chars"] > 0
    print(f"✓ 可观测：subagent_end 记录 {end['turns']} 轮、{end['chars']} 字符结论")

    # ---- 深度上限 ---------------------------------------------------------- #
    # 真实子代理的工具组里根本没有 delegate；这里额外在 Agent 层做一道防线。
    nested_agent = Agent(
        ScriptedLLM([reply("不会走到这里")]),
        AgentConfig(workspace=ws, mode=Mode.DEFAULT),
        depth=1,
        name="lead/sub",
    )
    nested = nested_agent.delegate_run("再派一个子代理", None)
    assert "不能再派生" in nested, nested
    print(f"✓ 深度上限：{nested}")

    print("\n结论：多代理不需要新协议，它只是「工具 + 新上下文 + 摘要回传」。")


if __name__ == "__main__":
    run()
    print("\nLAB06 PASS")
