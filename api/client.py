"""最小客户端：给测试、脚本和 CLI 用。

故意不引 requests：标准库 `urllib` 足够，也让读者看清 HTTP/SSE 的真实报文。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Iterator


class ApiError(RuntimeError):
    def __init__(self, status: int, payload: dict | str):
        super().__init__(f"HTTP {status}: {payload}")
        self.status = status
        self.payload = payload


class HarnessClient:
    def __init__(self, base_url: str, token: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    # -- 基础 -------------------------------------------------------------- #
    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = raw
            raise ApiError(exc.code, payload) from exc

    # -- API --------------------------------------------------------------- #
    def health(self) -> dict:
        return self._request("GET", "/health")

    def submit(self, task: str, session_id: str | None = None, **options) -> dict:
        payload = {"task": task, **options}
        if session_id:
            payload["session_id"] = session_id
        return self._request("POST", "/v1/runs", payload)

    def get(self, run_id: str) -> dict:
        return self._request("GET", f"/v1/runs/{run_id}")

    def wait(self, run_id: str, timeout: float = 60.0, interval: float = 0.1) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self.get(run_id)
            if status["status"] in {"done", "failed", "cancelled"}:
                return status
            time.sleep(interval)
        raise TimeoutError(f"run {run_id} 在 {timeout}s 内未结束")

    def events(self, run_id: str, after: int = 0) -> list[dict]:
        return self._request("GET", f"/v1/runs/{run_id}/events?after={after}")["events"]

    def stream(self, run_id: str, after: int = 0, timeout: float = 60.0) -> Iterator[dict]:
        """迭代 SSE 事件；`event: done` 时结束。"""
        request = urllib.request.Request(
            f"{self.base_url}/v1/runs/{run_id}/events?after={after}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "text/event-stream",
            },
        )
        deadline = time.time() + timeout
        with urllib.request.urlopen(request, timeout=timeout) as response:
            event_name = "message"
            for raw in response:
                if time.time() > deadline:
                    return
                line = raw.decode("utf-8", "replace").rstrip("\n")
                if line.startswith("event:"):
                    event_name = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    payload = line.split(":", 1)[1].strip()
                    if event_name == "done":
                        return
                    try:
                        yield json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                elif not line:
                    event_name = "message"

    def cancel(self, run_id: str, reason: str = "client_cancel") -> dict:
        return self._request("POST", f"/v1/runs/{run_id}/cancel", {"reason": reason})

    def approvals(self) -> list[dict]:
        return self._request("GET", "/v1/approvals")["approvals"]

    def approve(self, approval_id: str, approved: bool = True, by: str | None = None) -> dict:
        payload: dict = {"approved": approved}
        if by:
            payload["by"] = by
        return self._request("POST", f"/v1/approvals/{approval_id}", payload)

    def history(self, session_id: str) -> list[dict]:
        return self._request("GET", f"/v1/sessions/{session_id}/history")["messages"]
