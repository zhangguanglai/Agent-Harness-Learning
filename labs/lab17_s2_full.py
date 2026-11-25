"""实验 17：S2 正式项 —— JWT 鉴权、租户配额、双人审批与升级。

验证：
1. JWT(HS256) 签发的 token 能建 Principal；签名/tenant 缺失被拒；
2. 每日运行配额生效（第二次提交 429），/v1/usage 可查；
3. 安全评估路径要求两人审批，第一票后仍 pending，第二票才放行；
4. 审批 SLA 超时后升级，supervisor 可单独批准。

运行：python labs/lab17_s2_full.py
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import base64
import hashlib
import hmac
import json
import shutil
import threading
import time
import urllib.error
import urllib.request

from api.auth import TokenRegistry
from orchestrator import Orchestrator, make_server
from orchestrator.auth import JwtAuthenticator, decode_hs256
from orchestrator.quota import QuotaPolicy

TMP = _bootstrap.TMP_ROOT / "lab17"
SECRET = "lab-secret"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_jwt(claims: dict, secret: str = SECRET) -> str:
    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64(json.dumps(claims).encode())
    signature = b64(hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def http(base: str, method: str, path: str, body: dict | None = None, token: str | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


def start(orchestrator: Orchestrator):
    server = make_server(orchestrator, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def wait_status(base: str, run_id: str, token: str, wanted: set[str], timeout: float = 20) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        code, last = http(base, "GET", f"/v1/assessments/{run_id}", token=token)
        if code == 200 and last.get("status") in wanted:
            return last
        time.sleep(0.05)
    raise AssertionError(f"等待 {wanted} 超时：{last}")


def run() -> None:
    shutil.rmtree(TMP, ignore_errors=True)

    # ---- 1. JWT ---------------------------------------------------------- #
    authenticator = JwtAuthenticator(SECRET, issuer="lab", audience="harness")
    token = make_jwt({"sub": "alice", "tenant": "acme", "roles": ["operator"], "iss": "lab", "aud": "harness", "exp": time.time() + 300})
    principal = authenticator.authenticate(f"Bearer {token}")
    assert principal.tenant == "acme" and principal.user == "alice" and principal.roles == ("operator",)
    assert decode_hs256(token, SECRET)["tenant"] == "acme"
    try:
        authenticator.authenticate(f"Bearer {make_jwt({'tenant': 'acme'}, secret='wrong')}")
        raise AssertionError("错误签名不应通过")
    except Exception as exc:  # noqa: BLE001
        assert "签名" in str(exc), exc
    try:
        authenticator.authenticate(f"Bearer {make_jwt({'sub': 'x', 'iss': 'lab', 'aud': 'harness'})}")
        raise AssertionError("缺少 tenant 不应通过")
    except Exception as exc:  # noqa: BLE001
        assert "tenant" in str(exc)
    print("✓ JWT：claims → Principal，签名与 tenant 校验生效")

    jwt_orch = Orchestrator(TMP / "jwt", authenticator=authenticator)
    jwt_server, jwt_base = start(jwt_orch)
    try:
        code, submitted = http(jwt_base, "POST", "/v1/assessments", {"case_id": "CASE-01"}, token=token)
        assert code == 202, submitted
        code, _ = http(jwt_base, "GET", f"/v1/assessments/{submitted['run_id']}", token=make_jwt({"tenant": "acme"}, secret="wrong"))
        assert code == 401, code
        print("✓ JWT 接入：正确 token 可提交，错误签名 401")
    finally:
        jwt_server.shutdown()
        jwt_server.server_close()
        jwt_orch.store.close()

    # ---- 2. 配额 ---------------------------------------------------------- #
    quota_orch = Orchestrator(
        TMP / "quota",
        tokens=TokenRegistry.from_specs(["t=acme:alice:operator"]),
        quota_policy=QuotaPolicy(max_concurrent=3, daily_runs=1, daily_tokens=1_000_000),
    )
    quota_server, quota_base = start(quota_orch)
    try:
        code, first = http(quota_base, "POST", "/v1/assessments", {"case_id": "CASE-01"}, token="t")
        assert code == 202, first
        wait_status(quota_base, first["run_id"], "t", {"completed", "failed", "rejected"})
        code, second = http(quota_base, "POST", "/v1/assessments", {"case_id": "CASE-01"}, token="t")
        assert code == 429, (code, second)
        code, usage = http(quota_base, "GET", "/v1/usage", token="t")
        assert code == 200 and usage["usage"]["runs_today"] >= 1, usage
        assert usage["limits"]["daily_runs"] == 1
        print(f"✓ 配额：日运行上限 1，第二次 429；usage.runs_today={usage['usage']['runs_today']}")
    finally:
        quota_server.shutdown()
        quota_server.server_close()
        quota_orch.store.close()

    # ---- 3. 双人审批 ------------------------------------------------------ #
    tokens = TokenRegistry.from_specs(["t-alice=acme:alice:operator", "t-bob=acme:bob:supervisor"])
    dual = Orchestrator(
        TMP / "dual",
        tokens=tokens,
        approval_sla_seconds=30,
        second_approval_paths={"security_assessment"},
    )
    dual_server, dual_base = start(dual)
    try:
        code, submitted = http(dual_base, "POST", "/v1/assessments", {"case_id": "CASE-04"}, token="t-alice")
        assert code == 202, submitted
        run_id = submitted["run_id"]
        wait_status(dual_base, run_id, "t-alice", {"waiting_approval"})
        code, first_vote = http(dual_base, "POST", f"/v1/assessments/{run_id}/approve", {"approved": True}, token="t-alice")
        assert code == 202 and first_vote["status"] == "pending", first_vote
        assert first_vote["approvals"] == 1 and first_vote["required"] == 2
        code, second_vote = http(dual_base, "POST", f"/v1/assessments/{run_id}/approve", {"approved": True}, token="t-bob")
        assert code == 200 and second_vote["status"] == "approved", second_vote
        final = wait_status(dual_base, run_id, "t-alice", {"completed", "failed", "rejected"})
        assert final["status"] == "completed"
        print("✓ 双人审批：1/2 仍 pending，第二票后放行，结论 completed")

        # ---- 4. SLA 升级：supervisor 单人可批 ----------------------------- #
        code, submitted = http(dual_base, "POST", "/v1/assessments", {"case_id": "CASE-08"}, token="t-alice")
        run_id = submitted["run_id"]
        wait_status(dual_base, run_id, "t-alice", {"waiting_approval"})
        # 等 SLA 升级（lab 将 SLA 设得很短）
        dual.approval_sla_seconds = 0.4
        deadline = time.time() + 10
        escalated = False
        while time.time() < deadline:
            events = http(dual_base, "GET", f"/v1/assessments/{run_id}/events", token="t-alice")[1]["events"]
            if any(event["type"] == "approval.escalated" for event in events):
                escalated = True
                break
            time.sleep(0.1)
        assert escalated, "未观察到 approval.escalated"
        code, vote = http(dual_base, "POST", f"/v1/assessments/{run_id}/approve", {"approved": True}, token="t-bob")
        assert code == 200 and vote["status"] == "approved", vote
        wait_status(dual_base, run_id, "t-alice", {"completed", "failed", "rejected"})
        print("✓ 升级：SLA 超时 → approval.escalated → supervisor 单人批准")
    finally:
        dual_server.shutdown()
        dual_server.server_close()
        dual.store.close()
        shutil.rmtree(TMP, ignore_errors=True)

    print("\n结论：身份、配额、审批三件事都在控制面收口，领域代码不用改。")


if __name__ == "__main__":
    run()
    print("\nLAB17 PASS")
