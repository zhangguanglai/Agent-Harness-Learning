"""实验 10：HTTP API —— 鉴权、SSE、取消、审批、租户隔离。

验证：
1. `/health` 免鉴权；业务接口必须带 Bearer token，错误 token 返回 401；
2. 提交 → 轮询终态 → 事件 → 历史投影；
3. SSE 能把事件按序推给客户端，并以 `event: done` 收尾；
4. 取消经 API 传播到工具进程组；
5. 审批通过 API 完成（waiting_approval → approve → done）；
6. 跨租户访问一律 404；
7. 令牌桶限流生效。

运行：python labs/lab10_http_api.py
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import shutil
import threading
import time
from pathlib import Path

from api import ApiConfig, ApiError, HarnessAPI, HarnessClient, TokenRegistry
from api.auth import RateLimiter
from mini_harness import DemoLLM, ScriptedLLM, call, reply

TMP = _bootstrap.TMP_ROOT / "lab10"
SLOW = 'python -c "import time; time.sleep(20)"'


def llm_factory(payload: dict):
    if payload.get("scenario") == "slow":
        return ScriptedLLM([call("bash", command=SLOW), reply("done")])
    if payload.get("scenario") == "instant":
        return ScriptedLLM([reply("无需工具，直接回答。")])
    return DemoLLM()


def run() -> None:
    shutil.rmtree(TMP, ignore_errors=True)
    TMP.mkdir(parents=True)

    api = HarnessAPI(
        ApiConfig(port=0, workspace_root=TMP / "workspaces", workers=2, llm_factory=llm_factory),
        TokenRegistry.from_specs(["t-alice=acme:alice:operator", "t-bob=beta:bob:viewer"]),
    )
    httpd = api.build_httpd()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{api.port}"
    alice = HarnessClient(base, "t-alice", timeout=30)
    bob = HarnessClient(base, "t-bob", timeout=30)

    try:
        # ---- 1. 健康检查与鉴权 --------------------------------------------- #
        assert alice.health()["ok"] is True
        try:
            HarnessClient(base, "bad-token").submit("x", "s0")
            raise AssertionError("错误 token 不应通过")
        except ApiError as exc:
            assert exc.status == 401, exc.status
        print("✓ 鉴权：/health 免鉴权，错误 token 提交返回 401")

        # ---- 2. 提交 → 终态 → 事件 → 历史 ---------------------------------- #
        submitted = alice.submit("看看工作区并写 NOTES.md", "s1", mode="acceptEdits")
        run_id = submitted["run_id"]
        status = alice.wait(run_id, 30)
        assert status["status"] == "done", status
        assert status["turns"] >= 3
        events = alice.events(run_id)
        kinds = [e["kind"] for e in events]
        assert kinds[0] == "run_queued" and kinds[-1] == "run_finished", kinds[:2]
        messages = alice.history("s1")
        assert messages[0]["role"] == "user" and "NOTES.md" in messages[0]["content"]
        print(f"✓ 提交：{status['turns']} 轮完成，{len(events)} 条事件，历史 {len(messages)} 条消息")

        # ---- 3. SSE --------------------------------------------------------- #
        second = alice.submit("再写一次 NOTES.md", "s2", mode="acceptEdits")
        streamed = []
        for event in alice.stream(second["run_id"], timeout=30):
            streamed.append(event["kind"])
        assert len(streamed) >= 5, streamed
        assert streamed[-1] == "run_finished", streamed[-3:]
        print(f"✓ SSE：收到 {len(streamed)} 条事件，末条 run_finished，连接自动收尾")

        # ---- 4. 取消 -------------------------------------------------------- #
        slow = alice.submit("跑一个很慢的命令", "s3", scenario="slow", mode="bypassPermissions")
        for _ in range(100):
            if alice.get(slow["run_id"])["status"] == "running":
                break
            time.sleep(0.05)
        started = time.perf_counter()
        alice.cancel(slow["run_id"], "lab_cancel")
        final = alice.wait(slow["run_id"], 20)
        elapsed = time.perf_counter() - started
        assert final["status"] == "cancelled", final
        assert elapsed < 5, f"取消耗时 {elapsed:.1f}s"
        print(f"✓ 取消：{elapsed:.2f}s 内变为 cancelled")

        # ---- 5. 审批 -------------------------------------------------------- #
        approval_run = alice.submit("创建工作区文件", "s4", mode="default")
        approvals: list[dict] = []
        for _ in range(120):
            approvals = alice.approvals()
            if approvals:
                break
            time.sleep(0.05)
        assert approvals, "未等到审批请求"
        assert approvals[0]["tool"] == "write_file", approvals[0]["tool"]
        alice.approve(approvals[0]["id"], approved=True, by="alice@lab")
        approved_final = alice.wait(approval_run["run_id"], 20)
        assert approved_final["status"] == "done", approved_final
        assert (TMP / "workspaces" / "acme" / "s4" / "NOTES.md").exists()
        resolved = next(e for e in alice.events(approval_run["run_id"]) if e["kind"] == "approval_resolved")
        assert resolved["data"]["by"] == "alice@lab"
        print("✓ 审批：waiting_approval → API 批准（by=alice@lab）→ done，文件已落盘")

        # ---- 6. 租户隔离 ----------------------------------------------------- #
        try:
            bob.get(run_id)
            raise AssertionError("跨租户读取不应成功")
        except ApiError as exc:
            assert exc.status == 404, exc.status
        try:
            bob.approve(approvals[0]["id"], approved=True)
            raise AssertionError("跨租户审批不应成功")
        except ApiError as exc:
            assert exc.status == 404, exc.status
        print("✓ 隔离：跨租户读取与审批均返回 404（不泄露存在性）")

        # ---- 7. 限流 -------------------------------------------------------- #
        limiter = RateLimiter(capacity=1, rate_per_s=0.001)
        assert limiter.allow("k") is True
        assert limiter.allow("k") is False
        print("✓ 限流：令牌桶突发 1 次，第二次立即拒绝")
    finally:
        httpd.shutdown()
        api.shutdown()
        shutil.rmtree(TMP, ignore_errors=True)

    print("\n结论：API 层只做「鉴权、配额、编排、流式」，业务语义仍在内核与运行时里。")


if __name__ == "__main__":
    run()
    print("\nLAB10 PASS")
