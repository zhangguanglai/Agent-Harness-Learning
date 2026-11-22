"""实验 15：S0-5 —— OTLP 追踪串起编排 → worker → 工具。

验证：
1. 编排层产生 `assessment.run` / `agent.run` / `approval.wait` span；
2. worker 产生 `worker.run` 与 `worker.tool` span；
3. 全链路同一个 trace_id，父 span 关系正确；
4. worker span 带 token 计量（来自 mock 的 assistant message usage）；
5. 观测失败不影响业务（collector 不可达时 run 仍完成）。

运行：python labs/lab15_otel.py
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from deploy.otel.collector import SpanStore, make_server
from orchestrator import Orchestrator, make_server as make_orchestrator

ROOT = Path(__file__).resolve().parents[1]
WORKER_DIR = ROOT / "spike" / "dsh-worker"
TMP = _bootstrap.TMP_ROOT / "lab15"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def http_json(url: str, method: str = "GET", body: dict | None = None, timeout: float = 10) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={"content-type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_status(base: str, run_id: str, wanted: set[str], timeout: float = 30) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = http_json(f"{base}/v1/assessments/{run_id}")
        if last.get("status") in wanted:
            return last
        time.sleep(0.05)
    raise AssertionError(f"等待 {wanted} 超时，最后={last}")


def run() -> None:
    shutil.rmtree(TMP, ignore_errors=True)

    # ---- 1. collector + worker ------------------------------------------- #
    store = SpanStore()
    collector = make_server(store, port=0)
    threading.Thread(target=collector.serve_forever, daemon=True).start()
    otel_endpoint = f"http://127.0.0.1:{collector.server_address[1]}"

    worker_port = free_port()
    worker_env = {
        **os.environ,
        "DSH_BACKEND": "loopback",
        "PORT": str(worker_port),
        "MOCK_STEPS": "2",
        "MOCK_DELAY_MS": "20",
        "OTEL_EXPORTER_OTLP_ENDPOINT": otel_endpoint,
    }
    worker = subprocess.Popen(
        ["node", "src/server.js"],
        cwd=str(WORKER_DIR),
        env=worker_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            if http_json(f"http://127.0.0.1:{worker_port}/healthz", timeout=1).get("ok"):
                break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    else:
        worker.terminate()
        raise AssertionError("worker 未能启动")

    orchestrator = Orchestrator(
        TMP,
        worker_url=f"http://127.0.0.1:{worker_port}",
        worker_approval="auto",
        approval_timeout_s=30,
        otel_endpoint=otel_endpoint,
    )
    server = make_orchestrator(orchestrator, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        submitted = http_json(f"{base}/v1/assessments", "POST", {"case_id": "CASE-01", "tenant": "lab"})
        run_id = submitted["run_id"]
        final = wait_status(base, run_id, {"completed", "failed", "rejected"})
        assert final["status"] == "completed", final

        # 导出是异步的，等收集器把 span 收齐
        deadline = time.time() + 10
        spans: list[dict] = []
        while time.time() < deadline:
            spans = http_json(f"{otel_endpoint}/spans")["spans"]
            if {"assessment.run", "agent.run", "worker.run", "worker.tool"} <= {span["name"] for span in spans}:
                break
            time.sleep(0.1)
        by_name = {span["name"]: span for span in spans}
        for name in ("assessment.run", "agent.run", "worker.run", "worker.tool", "approval.wait"):
            if name not in by_name:
                print(f"… 缺少 span：{name}（已有 {sorted(by_name)}）")
        # CASE-01 免审批，approval.wait 不会出现
        for name in ("assessment.run", "agent.run", "worker.run", "worker.tool"):
            assert name in by_name, f"缺少 span {name}: {sorted(by_name)}"

        trace_ids = {span["trace_id"] for span in spans}
        assert len(trace_ids) == 1, f"trace_id 不一致：{trace_ids}"
        assert by_name["agent.run"]["parent_span_id"] == by_name["assessment.run"]["span_id"]
        assert by_name["worker.run"]["parent_span_id"] == by_name["agent.run"]["span_id"]
        assert by_name["worker.tool"]["parent_span_id"] == by_name["worker.run"]["span_id"]
        print("✓ 链路：assessment.run → agent.run → worker.run → worker.tool（同一 trace）")

        worker_span = by_name["worker.run"]
        assert int(worker_span["attributes"].get("llm.input_tokens", 0)) > 0
        assert worker_span["attributes"].get("run.status") == "succeeded"
        print(f"✓ 计量：worker.run token 输入={worker_span['attributes']['llm.input_tokens']}，状态={worker_span['attributes']['run.status']}")

        tool_span = by_name["worker.tool"]
        assert tool_span["attributes"].get("tool.name") == "kb.search"
        print("✓ 工具 span：tool.name=kb.search")

        # ---- 2. collector 不可达时业务不受影响 ---------------------------- #
        broken = Orchestrator(
            TMP / "broken",
            worker_url=f"http://127.0.0.1:{worker_port}",
            worker_approval="auto",
            otel_endpoint=f"http://127.0.0.1:{free_port()}",
        )
        broken_server = make_orchestrator(broken, port=0)
        threading.Thread(target=broken_server.serve_forever, daemon=True).start()
        broken_base = f"http://127.0.0.1:{broken_server.server_address[1]}"
        try:
            submitted = http_json(f"{broken_base}/v1/assessments", "POST", {"case_id": "CASE-07", "tenant": "lab"})
            done = wait_status(broken_base, submitted["run_id"], {"completed", "failed"})
            assert done["status"] == "completed", done
            print("✓ 韧性：collector 不可达时评估仍完成（观测失败不影响业务）")
        finally:
            broken_server.shutdown()
            broken_server.server_close()
            broken.store.close()

        print("\n结论：追踪不是日志——它把「谁调了谁、各花多久」变成可查询的结构。")
    finally:
        server.shutdown()
        server.server_close()
        orchestrator.store.close()
        worker.terminate()
        worker.wait(timeout=10)
        collector.shutdown()
        collector.server_close()
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    run()
    print("\nLAB15 PASS")
