"""实验 9：工具网关、审批状态机、角色权限、bench 门禁。

验证：
1. 只读工具的可重试错误会自动重试；有副作用的工具绝不自动重试；
2. `waiting_approval` 是可观测状态，批准人留痕；
3. 角色能力在工具面就生效（viewer 看不到写工具的执行权）；
4. bench 能作为 CI 门禁（通过率 + 退出码）。

运行：python labs/lab09_gateway_policy.py
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import shutil
import time
from pathlib import Path

from mini_harness import Mode, RetryableToolError, ScriptedLLM, Tool, ToolContext, build_core_registry, call, reply
from mini_harness.types import ToolCall
from runtime import ApprovalBroker, InMemoryStore, RunRequest, Runtime, ToolGateway
from runtime.policy import capabilities_for

TMP = _bootstrap.TMP_ROOT / "lab09"


def make_tool(name: str, *, read_only: bool, fail_times: int):
    """前 fail_times 次抛可重试错误，之后成功。"""
    state = {"n": 0}

    def handler(args: dict, ctx: ToolContext) -> str:
        state["n"] += 1
        if state["n"] <= fail_times:
            raise RetryableToolError("暂时不可用", code="TIMEOUT", hint="稍后重试")
        return f"{name} ok (第 {state['n']} 次)"

    return Tool(
        name=name,
        description=f"测试工具 {name}",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=handler,
        read_only=read_only,
        group="core",
    ), state


def run() -> None:
    shutil.rmtree(TMP, ignore_errors=True)
    ws = TMP / "ws"
    ws.mkdir(parents=True)
    (ws / "README.md").write_text("# demo\n", encoding="utf-8")
    ctx = ToolContext(workspace=ws)

    # ---- 1. 网关：只读工具重试，写工具不重试 -------------------------------- #
    registry = build_core_registry()
    flaky_read, read_state = make_tool("flaky_read", read_only=True, fail_times=2)
    flaky_write, write_state = make_tool("flaky_write", read_only=False, fail_times=1)
    registry.add(flaky_read)
    registry.add(flaky_write)
    gateway = ToolGateway(registry, max_attempts=3, backoff_s=0.0)

    read_result = gateway.invoke(ToolCall(name="flaky_read", arguments={}, id="c1"), ctx)
    assert read_result.ok and read_result.meta.get("attempts") == 3, read_result.meta
    assert read_state["n"] == 3

    write_result = gateway.invoke(ToolCall(name="flaky_write", arguments={}, id="c2"), ctx)
    assert not write_result.ok and write_result.meta.get("attempts") == 1, write_result.meta
    assert write_result.meta.get("retry_suppressed") == "side_effect_tool"
    print("✓ 网关：只读错误重试到成功（3 次），写操作重试被抑制（1 次）")

    # ---- 2. 审批状态机 ----------------------------------------------------- #
    broker = ApprovalBroker(auto_approve=False)
    runtime = Runtime(InMemoryStore(), workers=1, broker=broker)
    handle = runtime.submit(
        RunRequest(
            task="创建 hello.txt",
            session_id="s-approval",
            workspace=ws,
            mode=Mode.DEFAULT,
            llm=ScriptedLLM([call("write_file", path="hello.txt", content="hi"), reply("已写入")]),
        )
    )
    for _ in range(100):
        if handle.status.value == "waiting_approval":
            break
        time.sleep(0.05)
    assert handle.status.value == "waiting_approval", handle.status
    pending = broker.pending()
    assert len(pending) == 1 and pending[0].tool == "write_file"
    broker.resolve(pending[0].id, True, by="tester")
    assert handle.wait(15)
    assert handle.status.value == "done"
    kinds = [e.kind for e in handle.events()]
    assert "approval_requested" in kinds and "approval_resolved" in kinds
    resolved = next(e for e in handle.events() if e.kind == "approval_resolved")
    assert resolved.data.get("by") == "tester" and resolved.data.get("approved") is True
    print("✓ 审批：waiting_approval → 批准（by=tester）→ done，事件可追溯")

    # ---- 3. 角色权限 ------------------------------------------------------- #
    assert "write_file" not in (capabilities_for(("viewer",)) or set())
    viewer = runtime.submit(
        RunRequest(
            task="以 viewer 写文件",
            session_id="s-viewer",
            workspace=ws,
            roles=("viewer",),
            mode=Mode.BYPASS,
            llm=ScriptedLLM([call("write_file", path="nope.txt", content="x"), reply("没有权限")]),
        )
    )
    assert viewer.wait(15)
    assert not (ws / "nope.txt").exists()
    denied = [e for e in viewer.events() if e.kind == "permission_decision" and e.data.get("action") == "deny"]
    assert denied, "未记录角色拒绝事件"
    print("✓ 角色：viewer 的写调用被角色策略拒绝，文件未落盘")

    runtime.close()

    # ---- 4. bench 门禁 ---------------------------------------------------- #
    from bench.run import run_one
    from bench.tasks import TASKS

    bench_runtime = Runtime(InMemoryStore(), workers=1, broker=ApprovalBroker(auto_approve=True))
    try:
        record = run_one(bench_runtime, TASKS[0])
    finally:
        bench_runtime.close()
    assert record["passed"], record
    assert record["turns"] >= 2 and record["tool_calls"] >= 1
    print(f"✓ bench：{record['task']} 通过，{record['turns']} 轮 / {record['tool_calls']} 次工具调用")

    print("\n结论：网关管「怎么执行」，策略管「谁能执行」，审批管「此刻同不同意」。")


if __name__ == "__main__":
    run()
    print("\nLAB09 PASS")
