"""实验 13：S1 编排 —— 提交 → 评估 → 审批 → 定稿。

验证：
1. 免审批路径（CASE-01）自动完成并生成 final 产物；
2. 需审批路径（CASE-04）停在 waiting_approval，批准后才 final；
3. 拒绝（CASE-03）使 run 进入 rejected，不产出 final；
4. 事件流（SSE）按 seq 单调、以 done 收尾；
5. 引用全部可定位，注入样本被标记。

运行：python labs/lab13_orchestrator.py
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import json
import shutil
import threading
import time
import urllib.request
from pathlib import Path

from orchestrator import Orchestrator, make_server

TMP = _bootstrap.TMP_ROOT / "lab13"


def http_json(base: str, method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_status(base: str, run_id: str, wanted: set[str], timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = http_json(base, "GET", f"/v1/assessments/{run_id}")
        if last.get("status") in wanted:
            return last
        time.sleep(0.05)
    raise AssertionError(f"等待状态 {wanted} 超时，最后状态={last}")


def collect_sse(base: str, path: str, timeout: float = 15.0) -> list[dict]:
    request = urllib.request.Request(base + path, headers={"accept": "text/event-stream"})
    events: list[dict] = []
    deadline = time.time() + timeout
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for raw in response:
            if time.time() > deadline:
                break
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload:
                    events.append(json.loads(payload))
            elif line.startswith("event:"):
                if line[6:].strip() == "done":
                    break
    return events


def run() -> None:
    shutil.rmtree(TMP, ignore_errors=True)
    orchestrator = Orchestrator(TMP, approval_timeout_s=30)
    server = make_server(orchestrator, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        # ---- 1. 免审批：CASE-01 ------------------------------------------- #
        submitted = http_json(base, "POST", "/v1/assessments", {"case_id": "CASE-01", "tenant": "lab"})
        run_id = submitted["run_id"]
        final = wait_status(base, run_id, {"completed", "failed", "rejected"})
        assert final["status"] == "completed", final
        artifacts = http_json(base, "GET", f"/v1/assessments/{run_id}/artifacts")["artifacts"]
        finals = [item for item in artifacts if item["status"] == "final"]
        assert finals and Path(finals[0]["path"]).exists(), "缺少 final 产物"
        assert final["decision"]["path"] == "exempt_small"
        print(f"✓ 免审批：CASE-01 自动完成，final 产物 v{finals[0]['version']}")

        # ---- 2. 需审批：CASE-04 ------------------------------------------- #
        submitted = http_json(base, "POST", "/v1/assessments", {"case_id": "CASE-04", "tenant": "lab"})
        run_id = submitted["run_id"]
        waiting = wait_status(base, run_id, {"waiting_approval", "completed", "failed"})
        assert waiting["status"] == "waiting_approval", waiting
        pending = http_json(base, "GET", "/v1/approvals")["approvals"]
        assert any(item["run_id"] == run_id for item in pending), "待审批列表缺少该 run"

        http_json(base, "POST", f"/v1/assessments/{run_id}/approve",
                  {"approved": True, "by": "compliance.officer", "comment": "同意申报安全评估"})
        done = wait_status(base, run_id, {"completed", "rejected", "failed"})
        assert done["status"] == "completed", done
        assert done["decision"]["path"] == "security_assessment"
        events = http_json(base, "GET", f"/v1/assessments/{run_id}/events")["events"]
        types = [event["type"] for event in events]
        assert "approval.requested" in types and "approval.resolved" in types
        resolved = next(event for event in events if event["type"] == "approval.resolved")
        assert resolved["payload"]["approved"] is True and resolved["payload"]["by"] == "compliance.officer"
        print("✓ 审批：CASE-04 停在 waiting_approval → 批准 → completed（批准人留痕）")

        # ---- 3. 拒绝：CASE-03 -------------------------------------------- #
        submitted = http_json(base, "POST", "/v1/assessments", {"case_id": "CASE-03", "tenant": "lab"})
        run_id = submitted["run_id"]
        wait_status(base, run_id, {"waiting_approval"})
        http_json(base, "POST", f"/v1/assessments/{run_id}/approve",
                  {"approved": False, "by": "compliance.officer", "comment": "材料不足"})
        rejected = wait_status(base, run_id, {"rejected", "completed"})
        assert rejected["status"] == "rejected", rejected
        artifacts = http_json(base, "GET", f"/v1/assessments/{run_id}/artifacts")["artifacts"]
        assert not [item for item in artifacts if item["status"] == "final"], "拒绝后不应有 final 产物"
        print("✓ 拒绝：CASE-03 进入 rejected，未产出 final")

        # ---- 4. SSE ------------------------------------------------------- #
        submitted = http_json(base, "POST", "/v1/assessments", {"case_id": "CASE-10", "tenant": "lab"})
        run_id = submitted["run_id"]
        streamed = collect_sse(base, f"/v1/assessments/{run_id}/events")
        seqs = [event["seq"] for event in streamed]
        assert seqs == sorted(seqs) and len(seqs) >= 5
        assert streamed[-1]["type"] in {"assessment.completed", "assessment.rejected"}
        extract = next(event for event in streamed if event["type"] == "extract.completed")
        assert extract["payload"]["injection_detected"] is True
        print(f"✓ SSE：{len(streamed)} 条事件，seq 单调，注入样本被标记")

        # ---- 5. 引用 ------------------------------------------------------ #
        verdicts = [event for event in streamed if event["type"] == "citations.verified"]
        assert verdicts and verdicts[0]["payload"]["valid"] is True
        print("✓ 引用：citations.verified 全部可定位")

        print("\n结论：控制面只做「编排 + 审批 + 产物」，领域判定仍在工具里，责任边界清晰。")
    finally:
        server.shutdown()
        server.server_close()
        orchestrator.store.close()
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    run()
    print("\nLAB13 PASS")
