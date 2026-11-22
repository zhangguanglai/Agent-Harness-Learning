"""DSH worker 客户端：提交 run、消费 SSE、把事件回灌给编排层。

与 `spike/dsh-worker` 的契约见 [spike/dsh-worker/README.md](../../spike/dsh-worker/README.md)：
  POST /v1/runs  {run_id, tenant_id, task, workspace, approval_url}
  GET  /v1/runs/{id}/events   SSE
  POST /v1/runs/{id}/cancel

这里只做协议翻译，不做业务判断：worker 的事件是「agent 发生了什么」，
编排层的事件是「这次评估走到哪一步」。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


class WorkerError(RuntimeError):
    pass


class WorkerClient:
    def __init__(self, base_url: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def health(self) -> dict:
        return self._json("GET", "/healthz")

    def submit(
        self,
        *,
        run_id: str,
        task: str,
        approval_url: str | None,
        tenant: str = "demo",
        workspace: str | None = None,
        traceparent: str | None = None,
    ) -> dict:
        payload = {"run_id": run_id, "tenant_id": tenant, "task": task}
        if traceparent:
            payload["traceparent"] = traceparent
        if approval_url:
            payload["approval_url"] = approval_url
        if workspace:
            payload["workspace"] = workspace
        return self._json("POST", "/v1/runs", payload)

    def cancel(self, run_id: str, reason: str = "orchestrator_cancel") -> dict:
        return self._json("POST", f"/v1/runs/{run_id}/cancel", {"reason": reason})

    def events(self, run_id: str, after: int = 0) -> list[dict]:
        return self._json("GET", f"/v1/runs/{run_id}/events?after={after}")["events"]

    def stream(self, run_id: str, after: int = 0, timeout: float = 600.0):
        """逐条产出 worker 事件，遇到终态事件后停止。"""
        request = urllib.request.Request(
            f"{self.base_url}/v1/runs/{run_id}/events?after={after}",
            headers={"accept": "text/event-stream"},
        )
        deadline = time.time() + timeout
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for raw in response:
                if time.time() > deadline:
                    return
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("event:"):
                    if line[6:].strip() == "done":
                        return
                elif line.startswith("data:"):
                    payload = line[5:].strip()
                    if not payload:
                        continue
                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    yield event
                    if event.get("type") in {"run.succeeded", "run.failed", "run.cancelled", "run.timed_out"}:
                        return

    # -- 内部 -------------------------------------------------------------- #
    def _json(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={"content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise WorkerError(f"worker HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise WorkerError(f"worker 不可达：{exc.reason}") from exc
