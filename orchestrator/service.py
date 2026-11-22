"""编排服务：HTTP + 运行管理 + 审批闸门 + worker 集成 + SSE。

两种执行模式：
- 本地确定性 Agent（默认）：直接调 domain 工具；
- worker 模式（--worker-url）：先把「agent 阶段」交给 spike/dsh-worker 跑，
  事件回灌到同一事件流，然后继续本地评估与审批。

接口：
  POST /v1/assessments                提交评估
  GET  /v1/assessments/{id}           状态/判定
  GET  /v1/assessments/{id}/events    JSON 或 SSE
  POST /v1/assessments/{id}/approve   审批（对 pending 的审批记录生效）
  GET  /v1/assessments/{id}/artifacts 产物版本
  GET  /v1/approvals                  待审批列表
  POST /v1/worker-approvals           worker 的审批回调（同步返回决定）
  GET  /healthz
"""

from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from domain.banking_a import __file__ as domain_file

from observability import Tracer

from .store import Store
from .worker_client import WorkerClient, WorkerError
from .workflow import AssessmentWorkflow

DATA = Path(domain_file).parent / "data"
TERMINAL = {"completed", "rejected", "failed"}


@dataclass
class Gate:
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool = False
    by: str = ""
    comment: str = ""

    def wait(self, timeout: float) -> tuple[bool, str, str]:
        if not self.event.wait(timeout):
            return False, "timeout", "approval timed out"
        return self.approved, self.by, self.comment


class Orchestrator:
    def __init__(
        self,
        root: str | Path | None = None,
        *,
        approval_timeout_s: float = 600.0,
        worker_url: str | None = None,
        worker_approval: str = "auto",
        public_url: str | None = None,
        otel_endpoint: str | None = None,
    ):
        base = Path(root) if root else Path(__file__).parent / "_data"
        self.store = Store(base / "orchestrator.db", base / "artifacts")
        self.workflow = AssessmentWorkflow(
            self.store,
            corpus_dir=DATA / "corpus",
            cases_file=DATA / "cases" / "cases.md",
            rules_dir=DATA / "rules",
            approval_timeout_s=approval_timeout_s,
        )
        self.approval_timeout_s = approval_timeout_s
        self.worker = WorkerClient(worker_url) if worker_url else None
        self.worker_approval = worker_approval  # auto | wait
        self.public_url = public_url
        self.tracer = Tracer(otel_endpoint, service="orchestrator", attributes={"deployment": "s0"})
        self.spans: dict[str, object] = {}
        self.gates: dict[str, Gate] = {}
        self._lock = threading.Lock()

    # -- API --------------------------------------------------------------- #
    def submit(self, case_id: str, tenant: str = "demo") -> dict:
        if case_id not in self.workflow.cases:
            raise ValueError(f"未知案例：{case_id}")
        run = self.store.create_run(case_id, tenant)
        self.spans[run.id] = self.tracer.start(
            "assessment.run",
            attributes={"run.id": run.id, "case.id": case_id, "tenant.id": tenant},
        )
        self.store.append_event(run.id, "run.queued", {"case_id": case_id, "tenant": tenant})
        threading.Thread(target=self._run, args=(run.id, case_id), daemon=True).start()
        return {"run_id": run.id, "status": "running", "events_url": f"/v1/assessments/{run.id}/events"}

    def _run(self, run_id: str, case_id: str) -> None:
        try:
            self.store.append_event(run_id, "run.started", {"case_id": case_id})
            if self.worker is not None:
                self._run_agent(run_id, case_id)
            self.workflow.run(run_id, case_id, request_approval=self.request_approval)
        except Exception as exc:  # noqa: BLE001 —— 失败要落事件
            self.store.set_status(run_id, "failed", error=f"{type(exc).__name__}: {exc}")
            self.store.append_event(run_id, "assessment.failed", {"error": str(exc)})
        finally:
            run = self.store.get_run(run_id)
            pending = self.store.pending_approval(run_id)
            if pending and run and run.status in {"rejected", "failed"}:
                self.store.resolve_approval(pending["id"], False, by="timeout", comment="approval timed out")
            span = self.spans.pop(run_id, None)
            if span is not None:
                span.attributes["run.status"] = run.status if run else "unknown"
                span.end(error=run.error if run and run.error else None)

    # -- agent 阶段（worker） ---------------------------------------------- #
    def _run_agent(self, run_id: str, case_id: str) -> None:
        assert self.worker is not None
        task = f"评估案例 {case_id}：判断是否构成数据出境、适用哪条路径、需要哪些材料。"
        self.store.append_event(run_id, "agent.submitted", {"worker": self.worker.base_url, "task": task})
        run_span = self.spans.get(run_id)
        agent_span = self.tracer.start(
            "agent.run",
            parent=run_span.traceparent if run_span else None,
            attributes={"run.id": run_id, "worker": self.worker.base_url},
        )
        approval_url = f"{self.public_url}/v1/worker-approvals" if self.public_url else None
        try:
            self.worker.submit(
                run_id=run_id,
                task=task,
                approval_url=approval_url,
                tenant="demo",
                traceparent=agent_span.traceparent,
            )
        except WorkerError as exc:
            self.store.append_event(run_id, "agent.failed", {"error": str(exc)})
            raise

        terminal: dict | None = None
        try:
            for event in self.worker.stream(run_id, timeout=600):
                self.store.append_event(
                    run_id,
                    "agent.event",
                    {
                        "worker_type": event.get("type"),
                        "worker_seq": event.get("seq"),
                        "payload": event.get("payload"),
                    },
                )
                if event.get("type") in {"run.succeeded", "run.failed", "run.cancelled", "run.timed_out"}:
                    terminal = event
                    break
        except WorkerError as exc:
            self.store.append_event(run_id, "agent.failed", {"error": str(exc)})
            raise

        if terminal is None:
            self.store.append_event(run_id, "agent.failed", {"error": "worker stream ended without terminal event"})
            raise WorkerError("worker 未给出终态")
        status = terminal.get("type")
        if status != "run.succeeded":
            self.store.append_event(run_id, "agent.failed", {"worker_status": status, "payload": terminal.get("payload")})
            raise WorkerError(f"worker run 结束于 {status}")
        self.store.append_event(
            run_id,
            "agent.completed",
            {"worker_status": status, "final_response": (terminal.get("payload") or {}).get("final_response", "")},
        )
        agent_span.attributes["agent.status"] = status
        agent_span.end()

    # -- 审批 -------------------------------------------------------------- #
    def request_approval(self, run_id: str, action: str, extra: dict | None = None) -> tuple[bool, str, str]:
        approval = self.store.create_approval(run_id, action)
        gate = Gate()
        with self._lock:
            self.gates[approval["id"]] = gate
        self.store.set_status(run_id, "waiting_approval")
        self.store.append_event(
            run_id,
            "approval.requested",
            {"approval_id": approval["id"], "action": action, **(extra or {})},
        )
        run_span = self.spans.get(run_id)
        approval_span = self.tracer.start(
            "approval.wait",
            parent=run_span.traceparent if run_span else None,
            attributes={"run.id": run_id, "approval.action": action},
        )
        approved, by, comment = gate.wait(self.approval_timeout_s)
        approval_span.attributes["approval.approved"] = approved
        approval_span.attributes["approval.by"] = by
        approval_span.end()
        self.store.resolve_approval(approval["id"], approved, by, comment)
        self.store.append_event(
            run_id,
            "approval.resolved",
            {"approval_id": approval["id"], "approved": approved, "by": by, "comment": comment},
        )
        with self._lock:
            self.gates.pop(approval["id"], None)
        if self.store.get_run(run_id) and self.store.get_run(run_id).status == "waiting_approval":
            self.store.set_status(run_id, "running")
        return approved, by, comment

    def approve(self, run_id: str, approved: bool, by: str, comment: str = "") -> dict:
        pending = self.store.pending_approval(run_id)
        if not pending:
            raise ValueError("没有待审批的请求")
        resolved = self.store.resolve_approval(pending["id"], approved, by, comment)
        if resolved is None:
            raise ValueError("审批已被处理")
        with self._lock:
            gate = self.gates.get(pending["id"])
        if gate is None:
            raise ValueError("审批闸门不存在（可能已超时）")
        gate.approved = approved
        gate.by = by
        gate.comment = comment
        gate.event.set()
        return resolved

    def worker_approval_decision(self, body: dict) -> dict:
        """worker 的审批回调：auto 直接放行；wait 走与人工审批相同的闸门。"""
        run_id = str(body.get("run_id") or "")
        tool = str(body.get("tool") or "")
        if not self.store.get_run(run_id):
            raise ValueError(f"未知 run：{run_id}")
        if self.worker_approval == "auto":
            self.store.append_event(
                run_id, "agent.approval.auto", {"tool": tool, "policy": "worker_approval=auto"}
            )
            return {"approved": True, "by": "auto-policy"}
        approved, by, comment = self.request_approval(
            run_id, f"agent_tool:{tool}", {"tool": tool, "reason": body.get("reason", "")}
        )
        return {"approved": approved, "by": by, "comment": comment}

    # -- 查询 -------------------------------------------------------------- #
    def status(self, run_id: str) -> dict | None:
        run = self.store.get_run(run_id)
        if run is None:
            return None
        return {
            "run_id": run.id,
            "case_id": run.case_id,
            "tenant": run.tenant,
            "status": run.status,
            "decision": run.decision,
            "error": run.error,
        }

    def pending(self) -> list[dict]:
        return [
            pending
            for run in self._all_runs()
            if (pending := self.store.pending_approval(run["id"]))
        ]

    def _all_runs(self) -> list[dict]:
        return [{"id": run_id} for run_id in self.store.recent_runs()]


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class QuietServer(ThreadingHTTPServer):
    """SSE 客户端断开时不要打印栈：这是正常生命周期，不是服务错误。"""

    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def make_server(orchestrator: Orchestrator, host: str = "127.0.0.1", port: int = 8081) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            return

        def _json(self, code: int, payload: dict | list) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            length = int(self.headers.get("content-length") or 0)
            if length <= 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch(self._route_get)

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch(self._route_post)

        def _dispatch(self, fn) -> None:
            try:
                fn()
            except KeyError as exc:
                self._json(404, {"error": f"not found: {exc.args[0] if exc.args else ''}"})
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            except WorkerError as exc:
                self._json(502, {"error": str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": f"internal: {type(exc).__name__}"})

        def _route_get(self) -> None:
            url = urlsplit(self.path)
            path, query = url.path, parse_qs(url.query)
            if path == "/healthz":
                return self._json(200, {"ok": True, "runs": len(orchestrator._all_runs()), "worker": bool(orchestrator.worker)})
            if path == "/v1/approvals":
                return self._json(200, {"approvals": orchestrator.pending()})

            parts = path.strip("/").split("/")
            if len(parts) >= 3 and parts[0] == "v1" and parts[1] == "assessments":
                run_id = parts[2]
                if len(parts) == 3:
                    status = orchestrator.status(run_id)
                    return self._json(404, {"error": "not found"}) if status is None else self._json(200, status)
                if parts[3] == "events":
                    after = int((query.get("after") or ["0"])[0])
                    if "text/event-stream" in (self.headers.get("accept") or ""):
                        return self._stream(run_id, after)
                    return self._json(200, {"events": orchestrator.store.events(run_id, after)})
                if parts[3] == "artifacts":
                    return self._json(200, {"artifacts": orchestrator.store.artifacts(run_id)})
            return self._json(404, {"error": f"unknown path: {path}"})

        def _route_post(self) -> None:
            url = urlsplit(self.path)
            if url.path == "/v1/assessments":
                body = self._body()
                return self._json(202, orchestrator.submit(body.get("case_id", ""), body.get("tenant", "demo")))
            if url.path == "/v1/worker-approvals":
                return self._json(200, orchestrator.worker_approval_decision(self._body()))
            parts = url.path.strip("/").split("/")
            if len(parts) == 4 and parts[1] == "assessments" and parts[3] == "approve":
                body = self._body()
                result = orchestrator.approve(
                    parts[2], bool(body.get("approved")), str(body.get("by") or "reviewer"), str(body.get("comment") or "")
                )
                return self._json(200, result)
            return self._json(404, {"error": f"unknown path: {url.path}"})

        def _stream(self, run_id: str, after: int) -> None:
            self.send_response(200)
            self.send_header("content-type", "text/event-stream; charset=utf-8")
            self.send_header("cache-control", "no-cache")
            self.send_header("connection", "close")
            self.end_headers()
            self.close_connection = True
            last = after
            while True:
                events = orchestrator.store.events(run_id, last)
                for event in events:
                    self.wfile.write(f"id: {event['seq']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8"))
                    last = event["seq"]
                    if event["type"] in {"assessment.completed", "assessment.rejected", "assessment.failed"}:
                        self.wfile.write(b"event: done\ndata: {}\n\n")
                        self.wfile.flush()
                        return
                run = orchestrator.store.get_run(run_id)
                if run and run.status in TERMINAL:
                    self.wfile.write(b"event: done\ndata: {}\n\n")
                    self.wfile.flush()
                    return
                self.wfile.flush()
                time.sleep(0.05)

    server = QuietServer((host, port), Handler)
    if not orchestrator.public_url:
        orchestrator.public_url = f"http://{host}:{server.server_address[1]}"
    return server
