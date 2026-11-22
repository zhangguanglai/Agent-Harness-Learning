"""实验 3：上下文预算与压缩。

要看到的结论：
- 单条工具输出先被「头尾保留式」截断；
- 接近预算时折叠中段，保留系统提示、原始任务和最近若干轮；
- 压缩边界会清洗掉无主的 tool 消息（否则真实 provider 会 400）；
- 压缩这件事必须写进轨迹，否则「Agent 怎么突然失忆了」无法归因。

运行：python labs/lab03_context.py
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import shutil
from pathlib import Path

from mini_harness import Agent, AgentConfig, ContextManager, Mode, ScriptedLLM, call, reply
from mini_harness.types import ToolCall, assistant_message, system_message, tool_message, user_message

TMP = _bootstrap.TMP_ROOT / "lab03"


def prepare() -> Path:
    shutil.rmtree(TMP, ignore_errors=True)
    ws = TMP / "ws"
    ws.mkdir(parents=True)
    (ws / "big.txt").write_text("行内容 " * 1_500, encoding="utf-8")
    return ws


def assert_pairing(messages: list[dict]) -> None:
    """每个 tool 消息都必须能找到发起它的 assistant.tool_calls，否则真实 provider 会 400。"""
    pending: set[str] = set()
    for msg in messages:
        if msg.get("role") == "assistant":
            for call_obj in msg.get("tool_calls") or []:
                pending.add(str(call_obj.get("id")))
        elif msg.get("role") == "tool":
            cid = str(msg.get("tool_call_id"))
            assert cid in pending, f"孤儿 tool 消息：{msg.get('name')} / {cid}"
            pending.discard(cid)


def unit_check() -> None:
    ctx = ContextManager(max_context_tokens=600, compact_at=0.5, keep_tail=4, max_tool_chars=200)
    messages = [system_message("S" * 200), user_message("原始任务：读文件并总结")]
    for i in range(8):
        messages.append(
            assistant_message(
                f"第 {i} 轮：我去读文件",
                [ToolCall(name="read_file", arguments={"path": "big.txt"}, id=f"c{i}")],
            )
        )
        messages.append(tool_message(f"c{i}", "read_file", "R" * 800))
    before = ctx.used(messages)
    assert ctx.needs_compaction(messages)

    compacted, report = ctx.compact(messages)
    assert report is not None
    after = ctx.used(compacted)
    assert after < before, (before, after)
    assert compacted[0]["role"] == "system" and compacted[0]["content"] == "S" * 200
    assert compacted[1]["role"] == "user" and "原始任务" in compacted[1]["content"]
    assert compacted[2]["role"] == "assistant" and "上下文压缩" in compacted[2]["content"]
    assert_pairing(compacted)
    print(f"✓ 单元：{before} → {after} tokens，折叠 {report.dropped_messages} 条，工具调用与结果仍配对")


def integration_check(ws: Path) -> None:
    # 系统提示本身就占几百 token，所以这里故意把窗口设得很小，让压缩真的触发
    script = [call("read_file", path="big.txt") for _ in range(6)] + [reply("读完了，内容很长。")]
    agent = Agent(
        ScriptedLLM(script),
        AgentConfig(
            workspace=ws,
            mode=Mode.BYPASS,
            max_turns=10,
            max_context_tokens=2_000,
            compact_at=0.5,
            keep_tail=4,
            max_tool_chars=500,
        ),
    )
    result = agent.run("把 big.txt 读完并总结要点")
    assert result.stopped == "final", result.stopped
    assert result.compactions >= 1, "窗口只有 2000 token，必然触发压缩"

    events = [e for e in agent.tracer.events if e["kind"] == "compaction"]
    assert events, "压缩必须留下轨迹"
    first = events[0]
    assert first["after_tokens"] < first["before_tokens"]
    assert_pairing(result.messages)
    print(f"✓ 集成：触发 {result.compactions} 次压缩，轨迹记录 {first['before_tokens']} → {first['after_tokens']} tokens")

    # 工具输出确实被截断过（否则 800 字符会原样进入上下文）
    tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
    assert tool_msgs and all(len(m["content"]) <= 540 for m in tool_msgs[:3]), "单条工具输出应被截断"
    print(f"✓ 截断：工具消息最长 {max(len(m['content']) for m in tool_msgs)} 字符（上限 500 + 标记）")


def run() -> None:
    ws = prepare()
    unit_check()
    integration_check(ws)
    print("\n结论：压缩不是「摘要聊天记录」，而是保住三段关键内容后的有损折叠。")


if __name__ == "__main__":
    run()
    print("\nLAB03 PASS")
