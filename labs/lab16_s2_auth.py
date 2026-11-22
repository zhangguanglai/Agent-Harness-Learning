"""实验 16：S2 前奏 —— 鉴权、租户隔离、规则版本、产物抽象。

验证：
1. 未带 token → 401；/healthz 免鉴权；
2. 跨租户读取/审批 → 404（不泄露存在性）；
3. `rules_version=v2` 改变判定（CASE-05：v1 免申报 / v2 安全评估）；
4. 产物可按 run 查询，路径来自 ArtifactStore；
5. 内存 ArtifactStore 可直接替换本地目录（同一接口）。

运行：python labs/lab16_s2_auth.py
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import json
import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from api.auth import TokenRegistry
from orchestrator import Orchestrator, make_server
from orchestrator.artifacts import MemoryArtifactStore
from orchestrator.store import Store

TMP = _bootstrap.TMP_ROOT / "lab16"


def http(base: str, method: str, path: str, body: dict | None = None, token: str | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


def wait_status(base: str, run_id: str, token: str, wanted: set[str], timeout: float = 20) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        code, last = http(base, "GET", f"/v1/assessments/{run_id}", token=token)
        if code == 200 and last.get("status") in wanted:
            return last
        time.sleep(0.05)
    raise AssertionError(f"等待 {wanted} 超时，最后={last}")


def run() -> None:
    shutil.rmtree(TMP, ignore_errors=True)
    tokens = TokenRegistry.from_specs(["t-alice=acme:alice:operator", "t-bob=beta:bob:viewer"])
    orchestrator = Orchestrator(TMP, tokens=tokens, approval_timeout_s=30)
    server = make_server(orchestrator, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        # ---- 1. 鉴权 ---------------------------------------------------- #
        code, _ = http(base, "GET", "/healthz")
        assert code == 200
        code, payload = http(base, "POST", "/v1/assessments", {"case_id": "CASE-01"})
        assert code == 401, (code, payload)
        code, payload = http(base, "POST", "/v1/assessments", {"case_id": "CASE-01"}, token="bad")
        assert code == 401
        print("✓ 鉴权：/healthz 免鉴权，缺失/错误 token 均 401")

        # ---- 2. 规则版本 ------------------------------------------------- #
        code, submitted = http(base, "POST", "/v1/assessments", {"case_id": "CASE-05", "rules_version": "v1"}, token="t-alice")
        assert code == 202, submitted
        run_v1 = submitted["run_id"]
        v1 = wait_status(base, run_v1, "t-alice", {"completed", "rejected", "failed"})
        assert v1["status"] == "completed" and v1["decision"]["path"] == "exempt_small", v1
        assert v1["rules_version"] == "v1"

        code, submitted = http(base, "POST", "/v1/assessments",
                               {"case_id": "CASE-05", "rules_version": "v2"}, token="t-alice")
        assert code == 202, submitted
        run_v2 = submitted["run_id"]
        waiting = wait_status(base, run_v2, "t-alice", {"waiting_approval", "completed", "failed"})
        assert waiting["status"] == "waiting_approval", waiting
        assert waiting["rules_version"] == "v2"
        code, result = http(base, "POST", f"/v1/assessments/{run_v2}/approve",
                            {"approved": True, "by": "compliance.officer"}, token="t-alice")
        assert code == 200, result
        v2 = wait_status(base, run_v2, "t-alice", {"completed", "failed", "rejected"})
        assert v2["decision"]["path"] == "security_assessment", v2
        print("✓ 规则版本：CASE-05 在 v1 免申报、v2 判安全评估（同案例不同结论）")

        # ---- 3. 租户隔离 ------------------------------------------------- #
        code, _ = http(base, "GET", f"/v1/assessments/{run_v1}", token="t-bob")
        assert code == 404, code
        code, _ = http(base, "POST", f"/v1/assessments/{run_v2}/approve", {"approved": True}, token="t-bob")
        assert code == 404, code
        code, approvals = http(base, "GET", "/v1/approvals", token="t-bob")
        assert code == 200 and approvals["approvals"] == []
        print("✓ 隔离：跨租户读取/审批 404，审批列表按租户过滤")

        # ---- 4. 产物 ----------------------------------------------------- #
        code, artifacts = http(base, "GET", f"/v1/assessments/{run_v2}/artifacts", token="t-alice")
        assert code == 200
        finals = [item for item in artifacts["artifacts"] if item["status"] == "final"]
        assert finals, artifacts
        assert Path(finals[0]["path"]).exists()
        assert len(finals[0]["sha256"]) == 64
        print(f"✓ 产物：final v{finals[0]['version']}，sha256={finals[0]['sha256'][:12]}…，路径可读")

        # ---- 5. 存储抽象 -------------------------------------------------- #
        memory = Store(TMP / "memory.db", MemoryArtifactStore())
        stored = memory.write_artifact("run_demo", "report.md", "hello", status="final")
        assert stored["path"].startswith("mem://run_demo/")
        memory.close()
        print("✓ 抽象：MemoryArtifactStore 与 LocalArtifactStore 同接口，可替换")
    finally:
        server.shutdown()
        server.server_close()
        orchestrator.store.close()
        shutil.rmtree(TMP, ignore_errors=True)

    print("\n结论：鉴权认租户、版本认请求、产物认接口——三件事都不改领域代码。")


if __name__ == "__main__":
    run()
    print("\nLAB16 PASS")
