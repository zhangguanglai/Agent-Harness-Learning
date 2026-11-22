"""实验 4：记忆的冻结快照。

要看到的结论：
- system 提示在会话开始时读取 SOUL/MEMORY/USER 的快照并冻结；
- 会话中 `memory` 工具写盘成功，但本会话的 system 不变（prompt 前缀稳定）；
- 下一个会话才会读到新记忆 —— 这是产品语义，不是 bug。

运行：python labs/lab04_memory.py
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import shutil
import sys
from pathlib import Path

from mini_harness import Agent, AgentConfig, Mode, ScriptedLLM, reply, call

TMP = _bootstrap.TMP_ROOT / "lab04"


def run() -> None:
    shutil.rmtree(TMP, ignore_errors=True)
    ws = TMP / "ws"
    ws.mkdir(parents=True)
    home = TMP / "memory"

    # ---- 会话 A：写入一条用户偏好 ---------------------------------------- #
    agent = Agent(
        ScriptedLLM(
            [
                call("memory", op="add", file="user", text="用户偏好 pytest，讨厌 unittest"),
                reply("已记住你的偏好。"),
            ]
        ),
        AgentConfig(workspace=ws, mode=Mode.BYPASS, memory_home=home),
    )
    result = agent.run("记住：我偏好 pytest")
    assert result.stopped == "final", result.stopped

    user_file = home / "USER.md"
    assert user_file.exists(), "记忆文件应落盘"
    assert "pytest" in user_file.read_text(encoding="utf-8")

    system_prompt = result.messages[0]["content"]
    assert "pytest" not in system_prompt, "冻结快照：本会话的 system 不应包含刚写入的偏好"
    tool_msg = next(m for m in result.messages if m.get("role") == "tool")
    assert "快照" in tool_msg["content"], "工具返回值应提示冻结语义"
    print("✓ 会话内：文件已写入，但 system 提示仍是启动时快照")

    # ---- 会话 B：新 Agent 才能看到 ---------------------------------------- #
    fresh = Agent(ScriptedLLM([reply("好的")]), AgentConfig(workspace=ws, mode=Mode.BYPASS, memory_home=home))
    fresh_system = fresh._build_system("随便问一句")
    assert "pytest" in fresh_system, "新会话应读到新记忆"
    print("✓ 新会话：USER.md 的内容已进入 system 提示")

    # ---- 记忆上限 ---------------------------------------------------------- #
    tiny = Agent(ScriptedLLM([reply("ok")]), AgentConfig(workspace=ws, mode=Mode.BYPASS, memory_home=TMP / "mem2"))
    tiny.memory.max_chars = 120
    try:
        for i in range(20):
            tiny.memory.append("memory", f"第 {i} 条很长的记忆内容，用来触发上限保护")
        raise AssertionError("应当触发字符上限")
    except ValueError as exc:
        assert "上限" in str(exc)
    print("✓ 上限：超过 max_chars 时拒绝写入，要求先 consolidate")

    # ---- 重复写入去重 ------------------------------------------------------ #
    msg = tiny.memory.append("user", "偏好 pytest")
    again = tiny.memory.append("user", "偏好 pytest")
    assert "已包含" in again, again
    print(f"✓ 去重：{again}")

    print("\n结论：记忆的正确性来自「文件 + 快照 + 上限」三件小事，而不是向量库。")


if __name__ == "__main__":
    run()
    print("\nLAB04 PASS")
