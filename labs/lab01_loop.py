"""实验 1：代理循环与停止条件。

验证三件事：
1. 一轮 = 一次模型调用 + 工具执行 + 结果写回；
2. 模型不再请求工具时循环自然结束；
3. 轮次上限与 token 预算能硬性刹车（而不是靠模型自觉）。

运行：python labs/lab01_loop.py
"""

from __future__ import annotations

import _bootstrap  # noqa: F401 —— 设置 sys.path 与 UTF-8 输出
import shutil
from pathlib import Path

from mini_harness import Agent, AgentConfig, Mode, ScriptedLLM, call, reply

TMP = _bootstrap.TMP_ROOT / "lab01"


def prepare() -> Path:
    shutil.rmtree(TMP, ignore_errors=True)
    ws = TMP / "ws"
    ws.mkdir(parents=True)
    (ws / "README.md").write_text("# demo\n\n一个用于实验的工作区。\n", encoding="utf-8")
    (ws / "src").mkdir()
    (ws / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    return ws


def run() -> None:
    ws = prepare()

    # ---- 场景 A：两轮自然结束 ------------------------------------------- #
    llm = ScriptedLLM([call("list_dir", path="."), reply("工作区里有 README.md 和 src/")])
    agent = Agent(llm, AgentConfig(workspace=ws, mode=Mode.BYPASS, max_turns=5))
    result = agent.run("看看工作区里有什么")

    assert result.stopped == "final", result.stopped
    assert result.turns == 2, result.turns
    tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
    assert [m["name"] for m in tool_msgs] == ["list_dir"], tool_msgs
    assert "README.md" in tool_msgs[0]["content"], "工具结果必须真的写回上下文"
    assert result.messages[0]["role"] == "system"
    assert result.messages[-1]["role"] == "assistant"
    print(f"✓ 自然结束：{result.turns} 轮，工具结果已进入上下文")

    # ---- 场景 B：轮次上限 ------------------------------------------------ #
    loopy = ScriptedLLM([call("list_dir", path=".") for _ in range(10)])
    agent2 = Agent(loopy, AgentConfig(workspace=ws, mode=Mode.BYPASS, max_turns=3))
    r2 = agent2.run("无限列目录")
    assert r2.stopped == "max_turns", r2.stopped
    assert r2.turns == 3, r2.turns
    print(f"✓ 轮次上限生效：{r2.turns} 轮后停止，脚本模型只被消费了 {loopy.cursor} 步")

    # ---- 场景 C：token 预算 --------------------------------------------- #
    agent3 = Agent(
        ScriptedLLM([call("list_dir", path="."), call("list_dir", path=".")]),
        AgentConfig(workspace=ws, mode=Mode.BYPASS, max_turns=9, token_budget=1),
    )
    r3 = agent3.run("预算很小")
    assert r3.stopped == "budget", r3.stopped
    assert r3.turns == 1, r3.turns
    print("✓ token 预算生效：第 1 轮后即停止")

    # ---- 场景 D：轨迹里能看到每一轮 -------------------------------------- #
    kinds = [e["kind"] for e in agent.tracer.events]
    assert kinds.count("turn") == 2, kinds
    assert kinds.count("llm_response") == 2
    assert kinds.count("tool_result") == 1
    assert kinds[0] == "session_start" and kinds[-1] == "session_end"
    print(f"✓ 轨迹完整：{len(kinds)} 条事件，含 turn / llm_response / tool_result")

    print("\n结论：循环本身很小，价值全在「停止条件是否可观测」。")


if __name__ == "__main__":
    run()
    print("\nLAB01 PASS")
