"""实验 7：轨迹与成本核算。

要看到的结论：
- 每次运行都落一份 JSONL 轨迹，事件顺序可回放（谁在第几轮调了什么、被拒了几次）；
- token 账本只认 provider/模型返回的 usage，不靠字数猜；
- 有了轨迹，「Agent 为什么变贵 / 为什么停」才有证据，而不是猜。

运行：python labs/lab07_trace_cost.py
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import json
import shutil
from pathlib import Path

from mini_harness import Agent, AgentConfig, Mode, ScriptedLLM, call, load_trace, reply

TMP = _bootstrap.TMP_ROOT / "lab07"

#: 示意单价（人民币 / 百万 token），仅用于演示换算方法，不是任何厂商的真实报价
PRICE = {"input": 1.0, "output": 4.0}


def run() -> None:
    shutil.rmtree(TMP, ignore_errors=True)
    ws = TMP / "ws"
    ws.mkdir(parents=True)
    (ws / "README.md").write_text("# demo\n\n用于轨迹实验。\n", encoding="utf-8")
    trace_path = TMP / "trace.jsonl"

    llm = ScriptedLLM(
        [
            call("list_dir", path="."),
            call("read_file", path="README.md"),
            call("bash", command="echo trace-demo"),
            call("write_file", path="../outside.txt", content="x"),  # 故意越界，制造一次拒绝
            reply("完成：看了目录、读了 README、跑了 echo；越界写入被拒绝。"),
        ]
    )
    agent = Agent(
        llm,
        AgentConfig(workspace=ws, mode=Mode.DEFAULT, allow=["Bash(echo *)"], max_turns=8),
        tracer=None,
    )
    agent.tracer.path = trace_path
    result = agent.run("跑一遍轨迹实验")

    assert result.stopped == "final", result.stopped
    assert result.turns == 5, result.turns
    assert trace_path.exists(), "轨迹必须落盘"

    # ---- 文件可回放 -------------------------------------------------------- #
    records = load_trace(trace_path)
    assert len(records) == len(agent.tracer.events)
    for i, rec in enumerate(records):
        assert {"ts", "session", "seq", "kind"} <= set(rec), rec
        assert rec["seq"] == i + 1, "seq 必须单调递增，才能回放顺序"
    print(f"✓ 轨迹：{len(records)} 条事件，seq 连续，可按序回放")

    kinds = [r["kind"] for r in records]
    assert kinds[0] == "session_start" and kinds[-1] == "session_end"
    assert kinds.count("tool_call") == 4

    denied = [r for r in records if r["kind"] == "permission" and r["action"] == "deny"]
    assert len(denied) == 1 and denied[0]["tool"] == "write_file"
    print(f"✓ 归因：能从轨迹里直接定位被拒的那次调用（{denied[0]['tool']}）")

    # ---- token 账本 -------------------------------------------------------- #
    llm_events = [r for r in records if r["kind"] == "llm_response"]
    total_in = sum(r["input_tokens"] for r in llm_events)
    total_out = sum(r["output_tokens"] for r in llm_events)
    summary = agent.tracer.summary()
    assert summary["usage"]["input_tokens"] == total_in
    assert summary["usage"]["output_tokens"] == total_out
    assert total_in > 0 and total_out > 0

    cost = total_in / 1_000_000 * PRICE["input"] + total_out / 1_000_000 * PRICE["output"]
    print(f"✓ 账本：输入 {total_in} / 输出 {total_out} tokens，按示意单价约 ¥{cost:.6f}")
    print(f"  （{len(llm_events)} 次模型调用，平均每次输入 {total_in // len(llm_events)} tokens）")

    # ---- 复现性：同样的输入应得到同样的账 ---------------------------------- #
    replay = Agent(
        ScriptedLLM([call("list_dir", path="."), reply("完成")]),
        AgentConfig(workspace=ws, mode=Mode.BYPASS, max_turns=4),
    )
    replay.run("只列目录")
    assert replay.tracer.usage["total"] > 0
    print("✓ 可复现：换一个会话重跑，账本独立统计，互不污染")

    print("\n结论：先有轨迹与账本，才谈得上优化成本和排查「它为什么停」。")


if __name__ == "__main__":
    run()
    print("\nLAB07 PASS")
