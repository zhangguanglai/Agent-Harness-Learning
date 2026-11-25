"""实验 18：S2 尾声 —— S3 兼容产物存储与预签名下载。

验证：
1. SigV4 PUT 能被 S3 兼容服务接受（Authorization 头 + 路径）；
2. 预签名 URL 结构正确（X-Amz-* 参数 + 64 位签名），且能直接 GET 到对象；
3. 编排层产物路径变成 `s3://bucket/key`；
4. `GET /v1/artifacts/{id}/download` 对 S3 返回 302（Location 为预签名 URL），对本地返回 200 内容。

运行：python labs/lab18_s3_artifacts.py
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import hashlib
import json
import shutil
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from orchestrator import Orchestrator, make_server
from orchestrator.artifacts import LocalArtifactStore, S3ArtifactStore, S3Config

TMP = _bootstrap.TMP_ROOT / "lab18"


class StubS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_headers: list[dict] = []
        self.lock = threading.Lock()

    def server(self) -> ThreadingHTTPServer:
        store = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):  # noqa: A003
                return

            def do_PUT(self) -> None:  # noqa: N802
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length)
                auth = self.headers.get("authorization") or ""
                with store.lock:
                    store.objects[self.path] = body
                    store.put_headers.append({"path": self.path, "authorization": auth})
                if not auth.startswith("AWS4-HMAC-SHA256 Credential=AK/"):
                    return self._send(403, b"bad auth")
                return self._send(200, b"")

            def do_GET(self) -> None:  # noqa: N802
                path = urlsplit(self.path).path
                with store.lock:
                    body = store.objects.get(path)
                return self._send(200, body) if body is not None else self._send(404, b"missing")

            def _send(self, code: int, body: bytes) -> None:
                self.send_response(code)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def http(base: str, method: str, path: str, body: dict | None = None, *, follow: bool = True) -> tuple[int, dict, dict]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, method=method, headers={"content-type": "application/json"})
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=15) as response:
            raw = response.read()
            headers = dict(response.headers)
            try:
                return response.status, json.loads(raw.decode() or "{}"), headers
            except json.JSONDecodeError:
                return response.status, {"_raw": raw.decode("utf-8", "replace")}, headers
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw.decode() or "{}"), dict(exc.headers)
        except json.JSONDecodeError:
            return exc.code, {"_raw": raw.decode("utf-8", "replace")}, dict(exc.headers)


def wait_completed(base: str, run_id: str, timeout: float = 20) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        code, last, _ = http(base, "GET", f"/v1/assessments/{run_id}")
        if code == 200 and last.get("status") in {"completed", "failed", "rejected"}:
            return last
        time.sleep(0.05)
    raise AssertionError(f"等待完成超时：{last}")


def run() -> None:
    shutil.rmtree(TMP, ignore_errors=True)

    # ---- 1. S3 存储 + 预签名 --------------------------------------------- #
    stub = StubS3()
    s3_server = stub.server()
    threading.Thread(target=s3_server.serve_forever, daemon=True).start()
    endpoint = f"http://127.0.0.1:{s3_server.server_address[1]}"
    store = S3ArtifactStore(
        S3Config(endpoint=endpoint, bucket="artifacts", region="us-east-1", access_key="AK", secret_key="SK")
    )
    content = "# 评估结论\n\n结论：需申报安全评估。\n"
    stored = store.put("run_x", "report.md", content)
    assert stored.path == "s3://artifacts/run_x/report.md", stored.path
    assert stub.objects["/artifacts/run_x/report.md"].decode() == content
    assert stub.put_headers and stub.put_headers[0]["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AK/")
    assert stored.sha256 == hashlib.sha256(content.encode()).hexdigest()
    print("✓ S3 PUT：SigV4 头被接受，对象字节与 sha256 一致")

    url = store.presign("run_x/report.md", expires=600)
    query = parse_qs(urlsplit(url).query)
    assert len(query["X-Amz-Signature"][0]) == 64
    assert query["X-Amz-Expires"] == ["600"]
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    with urllib.request.urlopen(url, timeout=10) as response:
        assert response.read().decode() == content
    print(f"✓ 预签名：GET 成功，签名 {query['X-Amz-Signature'][0][:12]}…，600s 有效")

    # ---- 2. 编排层用 S3 存产物 -------------------------------------------- #
    orchestrator = Orchestrator(TMP / "s3", artifact_store=store)
    server = make_server(orchestrator, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        code, submitted, _ = http(base, "POST", "/v1/assessments", {"case_id": "CASE-01"})
        assert code == 202, submitted
        run_id = submitted["run_id"]
        assert wait_completed(base, run_id)["status"] == "completed"
        code, artifacts, _ = http(base, "GET", f"/v1/assessments/{run_id}/artifacts")
        final = [item for item in artifacts["artifacts"] if item["status"] == "final"][0]
        assert final["path"].startswith("s3://artifacts/"), final
        key = final["path"].split("/", 3)[3]
        assert f"/artifacts/{key}" in stub.objects, (final["path"], list(stub.objects))

        code, _, headers = http(base, "GET", f"/v1/artifacts/{final['id']}/download", follow=False)
        assert code == 302, (code, headers)
        location = headers.get("location") or headers.get("Location")
        assert location and "X-Amz-Signature=" in location
        with urllib.request.urlopen(location, timeout=10) as response:
            assert len(response.read()) > 0
        print("✓ 编排产物：path=s3://…，下载返回 302 预签名 URL，可直接 GET")
    finally:
        server.shutdown()
        server.server_close()
        orchestrator.store.close()

    # ---- 3. 本地存储仍可下载 ---------------------------------------------- #
    local = Orchestrator(TMP / "local", artifact_store=LocalArtifactStore(TMP / "local-artifacts"))
    server = make_server(local, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        code, submitted, _ = http(base, "POST", "/v1/assessments", {"case_id": "CASE-07"})
        run_id = submitted["run_id"]
        assert wait_completed(base, run_id)["status"] == "completed"
        code, artifacts, _ = http(base, "GET", f"/v1/assessments/{run_id}/artifacts")
        final = [item for item in artifacts["artifacts"] if item["status"] == "final"][0]
        code, payload, _ = http(base, "GET", f"/v1/artifacts/{final['id']}/download")
        assert code == 200 and payload.get("_raw", "").strip(), (code, payload)
        print("✓ 本地产物：下载返回 200 + 正文（同一接口两种后端）")
    finally:
        server.shutdown()
        server.server_close()
        local.store.close()
        s3_server.shutdown()
        s3_server.server_close()
        shutil.rmtree(TMP, ignore_errors=True)

    print("\n结论：产物存哪（本地/S3）对编排与审批透明，审计只需 sha256 + 定位符。")


if __name__ == "__main__":
    run()
    print("\nLAB18 PASS")
