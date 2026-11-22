"""实验 8：运行时骨架 —— 事件溯源、断点续跑、取消。

验证：
1. 一次 Run 的每个阶段都落成事件，且 seq 单调；
2. 只靠事件就能重建对话（等价于「进程崩了也能恢复」）；
3. 同一 session 追加第二轮时，历史从事件来；
4. 取消能穿透到工具进程组，几秒内停下。

运行：python labs/lab08_runtime.py
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import shutil
import time
from pathlib import Path

from mini_harness import DemoLLM, Mode, ScriptedLLM, call, reply
from runtime import ApprovalBroker, RunRequest, Runtime, SQLiteStore, project_messages, summarize_events

TMP = _bootstrap.TMP_ROOT / "lab08"


def run() -> None:
    shutil.rmtree(TMP, ignore_errors=True)
    ws = TMP / "ws"
    ws.mkdir(parents=True)
    (ws / "README.md").write_text("# demo\n\n用于运行时实验。\n", encoding="utf-8")

    db = TMP / "events.db"
    store = SQLiteStore(db)
    runtime = Runtime(store, workers=2, broker=ApprovalBroker(auto_approve=True))

    # ---- 1. 事件流 --------------------------------------------------------- #
    handle = runtime.submit(
        RunRequest(
            task="读 README.md 并写 NOTES.md",
            session_id="s1",
            workspace=ws,
            mode=Mode.ACCEPT_EDITS,
            llm=DemoLLM(),
        )
    )
    assert handle.wait(30), "运行未在 30s 内结束"
    assert handle.status.value == "done", handle.status

    events = handle.events()
    kinds = [e.kind for e in events]
    assert kinds[0] == "run_queued" and kinds[1] == "run_started", kinds[:3]
    assert "model_response" in kinds and "tool_result" in kinds and "turn_started" in kinds
    assert kinds[-1] == "run_finished", kinds[-1:]
    assert [e.seq for e in events] == sorted(e.seq for e in events)
    print(f"✓ 事件溯源：{len(events)} 条事件，seq 单调，末条为 run_finished")

    # ---- 2. 从事件重建对话 -------------------------------------------------- #
    messages = project_messages(events)
    assert messages[0]["role"] == "user" and "NOTES.md" in messages[0]["content"]
    assert any(m.get("role") == "tool" for m in messages)
    assert any(m.get("role") == "assistant" and m.get("content") for m in messages)
    summary = summarize_events(events)
    assert summary["tool_calls"] == 3, summary
    assert summary["input_tokens"] > 0
    print(f"✓ 投影：重建 {len(messages)} 条消息，工具调用 {summary['tool_calls']} 次，token {summary['input_tokens']}")

    # ---- 3. 模拟崩溃后恢复：换进程、只用数据库 ------------------------------ #
    runtime.close()
    store.close()

    store2 = SQLiteStore(db)
    runtime2 = Runtime(store2, workers=1, broker=ApprovalBroker(auto_approve=True))
    history = runtime2.history("s1")
    assert history and history[0]["content"].startswith("读 README.md"), history[:1]
    first_len = len(history)

    follow = runtime2.submit(
        RunRequest(
            task="再写一份 SUMMARY.md",
            session_id="s1",
            workspace=ws,
            mode=Mode.ACCEPT_EDITS,
            llm=ScriptedLLM([reply("第二轮已完成。")]),
        )
    )
    assert follow.wait(20)
    assert len(runtime2.history("s1")) > first_len
    print(f"✓ 断点续跑：重启后从事件重建 {first_len} 条历史，同会话追加第二轮")

    # ---- 4. 取消：穿透到工具进程组 ----------------------------------------- #
    slow = runtime2.submit(
        RunRequest(
            task="跑一个 20 秒的命令",
            session_id="s2",
            workspace=ws,
            mode=Mode.BYPASS,
            llm=ScriptedLLM([call("bash", command='python -c "import time; time.sleep(20)"'), reply("done")]),
        )
    )
    time.sleep(0.6)
    started = time.perf_counter()
    slow.cancel("lab_cancel")
    assert slow.wait(15), "取消后未及时结束"
    elapsed = time.perf_counter() - started
    assert slow.status.value == "cancelled", slow.status
    assert elapsed < 5, f"取消耗时 {elapsed:.1f}s，说明进程组没有被回收"
    last = slow.events()[-1]
    assert last.kind == "run_cancelled"
    print(f"✓ 取消：{elapsed:.2f}s 内停下，末条事件 run_cancelled（reason={last.data.get('reason')}）")

    runtime2.close()
    store2.close()

    print("\n结论：状态是事件的投影，所以「恢复」只是重放，不是特殊逻辑。")


if __name__ == "__main__":
    run()
    print("\nLAB08 PASS")
