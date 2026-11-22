"""实验 14：S1.5 —— 编排层 × DSH worker 的真实链路。

链路：Orchestrator ──HTTP──▶ dsh-worker ──(loopback)──▶ 事件流回传 ──▶ 评估/审批/产物

验证：
1. 提交后 agent 阶段由 worker 执行，事件回灌到同一事件流（agent.submitted / agent.event / agent.completed）；
2. worker 审批 auto 模式下，domain 审批照常生效（CASE-04）；
3. worker 审批 wait 模式下，worker 的 approval/request 走人工闸门（CASE-01，免 domain 审批）；
4. 最终产物与判定仍来自领域工具链。

运行：python labs/lab14_worker_integration.py
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import json
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from orchestrator import Orchestrator, make_server

ROOT = Path(__file__).resolve().parents[1]
WORKER_DIR = ROOT / "spike" / "dsh-worker"
TMP = _bootstrap.TMP_ROOT / "lab14"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def http_json(base: str, method: str, path: str, body: dict | None = None, timeout: float = 20) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        base + path, data=data, method=method, headers={"content-type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def start_worker(port: int, mock_approval: bool) -> subprocess.Popen:
    env = {
        "PATH": str(Path(sys.executable).parent) + ";" + __import__("os").environ.get("PATH", ""),
        "DSH_BACKEND": "loopback",
        "PORT": str(port),
        "MOCK_STEPS": "2",
        "MOCK_DELAY_MS": "20",
        "MOCK_APPROVAL": "1" if mock_approval else "0",
    }
    proc = subprocess.Popen(
        ["node", "src/server.js"],
        cwd=str(WORKER_DIR),
        env={**__import__("os").environ, **env},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            if http_json(f"http://127.0.0.1:{port}", "GET", "/healthz", timeout=1).get("ok"):
                return proc
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    proc.terminate()
    raise AssertionError("worker 未能启动")


def wait_status(base: str, run_id: str, wanted: set[str], timeout: float = 20) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = http_json(base, "GET", f"/v1/assessments/{run_id}")
        if last.get("status") in wanted:
            return last
        time.sleep(0.05)
    raise AssertionError(f"等待 {wanted} 超时，最后={last}")


def wait_pending(base: str, keyword: str, timeout: float = 20) -> list[dict]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        approvals = http_json(base, "GET", "/v1/approvals")["approvals"]
        if any(keyword in item["action"] for item in approvals):
            return approvals
        time.sleep(0.05)
    raise AssertionError(f"未等到 {keyword} 审批")


def run() -> None:
    shutil.rmtree(TMP, ignore_errors=True)
    node = shutil.which("node")
    assert node, "需要 node 在 PATH"

    # ---- 1. worker 审批 auto + domain 审批 --------------------------------- #
    worker_port = free_port()
    worker = start_worker(worker_port, mock_approval=False)
    orchestrator = Orchestrator(
        TMP / "auto", worker_url=f"http://127.0.0.1:{worker_port}", worker_approval="auto", approval_timeout_s=60
    )
    server = make_server(orchestrator, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        submitted = http_json(base, "POST", "/v1/assessments", {"case_id": "CASE-04", "tenant": "lab"})
        run_id = submitted["run_id"]
        waiting = wait_status(base, run_id, {"waiting_approval", "failed"})
        assert waiting["status"] == "waiting_approval", waiting
        events = http_json(base, "GET", f"/v1/assessments/{run_id}/events")["events"]
        types = [event["type"] for event in events]
        for kind in ("agent.submitted", "agent.event", "agent.completed"):
            assert kind in types, f"缺少 {kind}: {types}"
        http_json(base, "POST", f"/v1/assessments/{run_id}/approve",
                  {"approved": True, "by": "officer", "comment": "ok"})
        done = wait_status(base, run_id, {"completed", "rejected", "failed"})
        assert done["status"] == "completed", done
        assert done["decision"]["path"] == "security_assessment"
        agent_events = [event for event in http_json(base, "GET", f"/v1/assessments/{run_id}/events")["events"] if event["type"] == "agent.event"]
        assert len(agent_events) >= 3, f"worker 事件回灌不足：{len(agent_events)}"
        print(f"✓ worker(auto)：CASE-04 经 worker 执行（{len(agent_events)} 条 worker 事件回灌），domain 审批后 completed")

        # ---- 2. worker 审批 wait：只审批 worker 的工具调用 ------------------ #
        shutil.rmtree(TMP / "wait", ignore_errors=True)
        wait_port = free_port()
        wait_worker = start_worker(wait_port, mock_approval=True)
        waiting_orch = Orchestrator(
            TMP / "wait", worker_url=f"http://127.0.0.1:{wait_port}", worker_approval="wait", approval_timeout_s=60
        )
        waiting_server = make_server(waiting_orch, port=0)
        threading.Thread(target=waiting_server.serve_forever, daemon=True).start()
        wait_base = f"http://127.0.0.1:{waiting_server.server_address[1]}"
        try:
            submitted = http_json(wait_base, "POST", "/v1/assessments", {"case_id": "CASE-01", "tenant": "lab"})
            run_id = submitted["run_id"]
            approvals = wait_pending(wait_base, "agent_tool")
            target = next(item for item in approvals if "agent_tool" in item["action"])
            http_json(wait_base, "POST", f"/v1/assessments/{run_id}/approve",
                      {"approved": True, "by": "lab", "comment": "批准工具调用"})
            done = wait_status(wait_base, run_id, {"completed", "failed", "rejected"})
            assert done["status"] == "completed", done
            events = http_json(wait_base, "GET", f"/v1/assessments/{run_id}/events")["events"]
            resolved = [event for event in events if event["type"] == "approval.resolved"]
            assert any(event["payload"]["by"] == "lab" for event in resolved), resolved
            assert any(event["payload"]["action"].startswith("agent_tool") for event in events if event["type"] == "approval.requested")
            print("✓ worker(wait)：worker 的 approval/request 走人工闸门，批准后继续并完成")
        finally:
            waiting_server.shutdown()
            waiting_server.server_close()
            waiting_orch.store.close()
            wait_worker.terminate()
            wait_worker.wait(timeout=10)

        print("\n结论：控制面与 worker 只交换「事件 + 审批回调」，领域判定与产物仍归编排层。")
    finally:
        server.shutdown()
        server.server_close()
        orchestrator.store.close()
        worker.terminate()
        worker.wait(timeout=10)
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    run()
    print("\nLAB14 PASS")
