"""HTTP API：把 Runtime 暴露成一个可被外部系统调用的服务。

只依赖标准库（`http.server` + `ThreadingHTTPServer`），因为我们不想为了教学引入
Web 框架；但接口形状是生产要用的那套：

    POST   /v1/runs                     提交（202）
    GET    /v1/runs                     列出本租户的运行
    GET    /v1/runs/{id}                状态与结果
    GET    /v1/runs/{id}/events         JSON（?after=seq）或 SSE（Accept: text/event-stream）
    POST   /v1/runs/{id}/cancel         取消（协作式，穿透到工具进程组）
    GET    /v1/approvals                本租户待审批列表
    POST   /v1/approvals/{id}           批准/拒绝
    GET    /v1/sessions/{id}/history    从事件投影出的历史（断点续跑/审计）
    GET    /health                      健康检查（免鉴权）

三条安全约束：
1. 角色只来自 token，请求体不能带 `roles`；
2. 运行与审批都按租户隔离，跨租户一律 404（不泄露存在性）；
3. 工作区路径由服务端拼接并校验，客户端只能给 session 名。
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from mini_harness.llm import LLM
from mini_harness.policy import Mode

from runtime import (
    ApprovalBroker,
    InMemoryStore,
    Runtime,
    RunRequest,
    SQLiteStore,
)
from runtime.events import Event
from runtime.store import project_messages

from .auth import AuthError, ForbiddenError, Principal, RateLimiter, TokenRegistry

API_VERSION = "0.2.0"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass
class ApiConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    workspace_root: Path = field(default_factory=lambda: Path.cwd() / ".harness" / "workspaces")
    db_path: Path | None = None
    workers: int = 2
    max_concurrent_per_tenant: int = 3
    approval_timeout_s: float = 300.0
    sse_timeout_s: float = 120.0
    max_body_bytes: int = 1_000_000
    rate_capacity: float = 30.0
    rate_per_s: float = 15.0
    #: 测试/定制用：按请求体构造 LLM；默认 DemoLLM
    llm_factory: Callable[[dict], LLM] | None = None


class HarnessAPI:
    def __init__(self, config: ApiConfig, tokens: TokenRegistry):
        self.config = config
        self.tokens = tokens
        self.limiter = RateLimiter(capacity=config.rate_capacity, rate_per_s=config.rate_per_s)
        store = SQLiteStore(config.db_path) if config.db_path else InMemoryStore()
        self.store = store
        self.runtime = Runtime(
            store,
            workers=config.workers,
            broker=ApprovalBroker(auto_approve=False),
            on_event=None,
        )
        self._httpd: ThreadingHTTPServer | None = None

    # ------------------------------------------------------------------ #
    # 业务方法（HTTP 之外的测试也可直接调）
    # ------------------------------------------------------------------ #
    def submit(self, principal: Principal, payload: dict) -> dict:
        task = str(payload.get("task") or "").strip()
        if not task:
            raise ValueError("task 不能为空")
        session_id = str(payload.get("session_id") or f"session-{int(time.time() * 1000)}")
        if not _SAFE_ID.match(session_id):
            raise ValueError("session_id 只允许字母数字与 ._-，长度 1–64")

        active = self.runtime.active_runs(principal.tenant)
        if len(active) >= self.config.max_concurrent_per_tenant:
            raise RuntimeError(
                f"租户 {principal.tenant} 的并发运行已达上限 {self.config.max_concurrent_per_tenant}"
            )

        mode = Mode(str(payload.get("mode") or "default"))
        workspace = self._workspace_for(principal, session_id)
        request = RunRequest(
            task=task,
            session_id=session_id,
            tenant=principal.tenant,
            user=principal.user,
            roles=principal.roles,  # 只来自 token
            workspace=workspace,
            mode=mode,
            allow=tuple(payload.get("allow") or ()),
            deny=tuple(payload.get("deny") or ()),
            ask=tuple(payload.get("ask") or ()),
            max_turns=int(payload.get("max_turns") or 16),
            token_budget=int(payload.get("token_budget") or 120_000),
            system_extra=str(payload.get("system_extra") or ""),
            approval_timeout_s=self.config.approval_timeout_s,
            llm=self.config.llm_factory(payload) if self.config.llm_factory else None,
        )
        handle = self.runtime.submit(request)
        return {
            "run_id": handle.run_id,
            "session_id": handle.session_id,
            "status": handle.status.value,
            "events_url": f"/v1/runs/{handle.run_id}/events",
        }

    def require_run(self, principal: Principal, run_id: str):
        handle = self.runtime.get(run_id)
        if handle is None or handle.request.tenant != principal.tenant:
            raise KeyError(run_id)
        return handle

    def status(self, principal: Principal, run_id: str) -> dict:
        handle = self.require_run(principal, run_id)
        result = handle.result
        return {
            "run_id": handle.run_id,
            "session_id": handle.session_id,
            "tenant": handle.request.tenant,
            "user": handle.request.user,
            "status": handle.status.value,
            "cancelled": handle.status.value == "cancelled",
            "error": handle.error,
            "text": result.text if result else "",
            "stopped": result.stopped if result else "",
            "turns": result.turns if result else 0,
            "usage": {
                "input_tokens": result.usage.input_tokens if result else 0,
                "output_tokens": result.usage.output_tokens if result else 0,
            },
        }

    def events(self, principal: Principal, run_id: str, after: int = 0) -> list[dict]:
        handle = self.require_run(principal, run_id)
        return [e.to_dict() for e in self.runtime.events(handle.run_id) if e.seq > after]

    def cancel(self, principal: Principal, run_id: str, reason: str = "api_cancel") -> dict:
        handle = self.require_run(principal, run_id)
        ok = self.runtime.cancel(handle.run_id, reason)
        return {"cancelled": ok, "status": handle.status.value}

    def list_runs(self, principal: Principal) -> list[dict]:
        rows = []
        for handle in self.runtime.handles():
            if handle.request.tenant != principal.tenant:
                continue
            rows.append(
                {
                    "run_id": handle.run_id,
                    "session_id": handle.session_id,
                    "status": handle.status.value,
                    "task": handle.request.task[:120],
                }
            )
        return rows[-50:]

    def pending_approvals(self, principal: Principal) -> list[dict]:
        rows = []
        for request in self.runtime.broker.pending():
            handle = self.runtime.get(request.run_id)
            if handle is None or handle.request.tenant != principal.tenant:
                continue
            rows.append(
                {
                    "id": request.id,
                    "run_id": request.run_id,
                    "session_id": request.session_id,
                    "tool": request.tool,
                    "args": request.args,
                    "reason": request.reason,
                    "created_at": request.created_at,
                }
            )
        return rows

    def resolve_approval(self, principal: Principal, approval_id: str, payload: dict) -> dict:
        request = self.runtime.broker.get(approval_id)
        if request is None:
            raise KeyError(approval_id)
        handle = self.runtime.get(request.run_id)
        if handle is None or handle.request.tenant != principal.tenant:
            raise KeyError(approval_id)
        approved = bool(payload.get("approved"))
        by = str(payload.get("by") or principal.user)
        resolved = self.runtime.broker.resolve(approval_id, approved, by=by)
        return {"resolved": resolved, "approved": approved, "by": by}

    def history(self, principal: Principal, session_id: str) -> list[dict]:
        if not _SAFE_ID.match(session_id):
            raise ValueError("无效的 session_id")
        events = [e for e in self.store.load_events(session_id) if e.run_id]
        # 租户隔离：只返回本租户 run 的事件
        allowed = {h.run_id for h in self.runtime.handles() if h.request.tenant == principal.tenant}
        filtered = [e for e in events if e.run_id in allowed]
        if not filtered:
            raise KeyError(session_id)
        return project_messages(filtered)

    # ------------------------------------------------------------------ #
    # 服务生命周期
    # ------------------------------------------------------------------ #
    def _workspace_for(self, principal: Principal, session_id: str) -> Path:
        base = (self.config.workspace_root / principal.tenant).resolve()
        workspace = (base / session_id).resolve()
        if workspace != base and not workspace.is_relative_to(base):
            raise ValueError("工作区路径越界")
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    def build_httpd(self) -> ThreadingHTTPServer:
        api = self

        class Handler(_Handler):
            server_api = api

        httpd = ThreadingHTTPServer((self.config.host, self.config.port), Handler)
        httpd.daemon_threads = True
        self._httpd = httpd
        return httpd

    def serve_forever(self) -> None:
        httpd = self._httpd or self.build_httpd()
        httpd.serve_forever(poll_interval=0.2)

    @property
    def port(self) -> int:
        return self._httpd.server_address[1] if self._httpd else self.config.port

    def shutdown(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        self.runtime.close()
        self.store.close()


class _Handler(BaseHTTPRequestHandler):
    server_api: HarnessAPI
    protocol_version = "HTTP/1.1"
    server_version = f"mini-harness/{API_VERSION}"

    # -- 基础工具 ---------------------------------------------------------- #
    def log_message(self, fmt: str, *args) -> None:  # 安静一点，测试输出才好看
        return

    def _json(self, code: int, payload: dict | list) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > self.server_api.config.max_body_bytes:
            raise ValueError("请求体过大")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"请求体不是合法 JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return payload

    def _principal(self, scope: str) -> Principal | None:
        api = self.server_api
        try:
            principal = api.tokens.authenticate(self.headers.get("Authorization"))
        except AuthError as exc:
            self._json(401, {"error": str(exc)})
            return None
        if not principal.has(scope):
            self._json(403, {"error": f"缺少 scope: {scope}"})
            return None
        if not api.limiter.allow(principal.token):
            self._json(429, {"error": "请求过于频繁，请稍后重试", "retry_after_s": 1})
            return None
        return principal

    def _dispatch(self, fn) -> None:
        try:
            fn()
        except KeyError as exc:
            self._json(404, {"error": f"未找到: {exc.args[0] if exc.args else ''}"})
        except PermissionError as exc:
            self._json(403, {"error": str(exc)})
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except RuntimeError as exc:
            self._json(429, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as exc:  # noqa: BLE001 —— API 不能把栈泄漏给客户端
            self._json(500, {"error": f"内部错误: {type(exc).__name__}"})

    # -- 路由 -------------------------------------------------------------- #
    def do_GET(self) -> None:  # noqa: N802
        self._dispatch(self._route_get)

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch(self._route_post)

    def _route_get(self) -> None:
        path = urlsplit(self.path).path
        query = parse_qs(urlsplit(self.path).query)

        if path == "/health":
            return self._json(200, {"ok": True, "version": API_VERSION, "time": time.time()})

        principal = self._principal("runs:read")
        if principal is None:
            return

        if path == "/v1/runs":
            return self._json(200, {"runs": self.server_api.list_runs(principal)})

        match = re.fullmatch(r"/v1/runs/([A-Za-z0-9_-]+)", path)
        if match:
            return self._json(200, self.server_api.status(principal, match.group(1)))

        match = re.fullmatch(r"/v1/runs/([A-Za-z0-9_-]+)/events", path)
        if match:
            run_id = match.group(1)
            after = int((query.get("after") or ["0"])[0])
            accept = (self.headers.get("Accept") or "").lower()
            if "text/event-stream" in accept or (query.get("stream") or [""])[0] in {"1", "true"}:
                return self._stream_events(principal, run_id, after)
            return self._json(200, {"events": self.server_api.events(principal, run_id, after)})

        match = re.fullmatch(r"/v1/sessions/([A-Za-z0-9._-]+)/history", path)
        if match:
            return self._json(200, {"messages": self.server_api.history(principal, match.group(1))})

        if path == "/v1/approvals":
            return self._json(200, {"approvals": self.server_api.pending_approvals(principal)})

        return self._json(404, {"error": f"未知路径: {path}"})

    def _route_post(self) -> None:
        path = urlsplit(self.path).path
        if path == "/v1/runs":
            principal = self._principal("runs:write")
            if principal is None:
                return
            payload = self._read_json()
            return self._json(202, self.server_api.submit(principal, payload))

        match = re.fullmatch(r"/v1/runs/([A-Za-z0-9_-]+)/cancel", path)
        if match:
            principal = self._principal("runs:write")
            if principal is None:
                return
            payload = self._read_json()
            return self._json(202, self.server_api.cancel(principal, match.group(1), str(payload.get("reason") or "api_cancel")))

        match = re.fullmatch(r"/v1/approvals/([A-Za-z0-9_-]+)", path)
        if match:
            principal = self._principal("approvals:write")
            if principal is None:
                return
            payload = self._read_json()
            return self._json(200, self.server_api.resolve_approval(principal, match.group(1), payload))

        return self._json(404, {"error": f"未知路径: {path}"})

    # -- SSE --------------------------------------------------------------- #
    def _stream_events(self, principal: Principal, run_id: str, after: int) -> None:
        handle = self.server_api.require_run(principal, run_id)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True

        last = after
        deadline = time.time() + self.server_api.config.sse_timeout_s
        last_ping = time.time()
        self.wfile.write(b"retry: 1000\n\n")
        while time.time() < deadline:
            events = [
                e
                for e in self.server_api.runtime.events(handle.run_id)
                if e.seq > last
            ]
            for event in events:
                self.wfile.write(f"id: {event.seq}\ndata: {event.to_json()}\n\n".encode("utf-8"))
                last = event.seq
            if events:
                self.wfile.flush()
                last_ping = time.time()
                if any(e.kind in {"run_finished", "run_cancelled", "run_failed"} for e in events):
                    self.wfile.write(f"event: done\ndata: {{\"status\": \"{handle.status.value}\"}}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    return
            elif time.time() - last_ping > 10:
                self.wfile.write(b": ping\n\n")
                self.wfile.flush()
                last_ping = time.time()
            time.sleep(0.05)
