"""编排服务：HTTP + 运行管理 + 审批闸门 + SSE。

接口（与 docs/15 的控制面契约对齐）：
  POST /v1/assessments                提交一次评估
  GET  /v1/assessments/{id}           状态/判定
  GET  /v1/assessments/{id}/events    JSON 或 SSE
  POST /v1/assessments/{id}/approve   审批
  GET  /v1/assessments/{id}/artifacts 产物版本
  GET  /v1/approvals                  待审批列表
  GET  /healthz
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from domain.banking_a import __file__ as domain_file

from .store import Store
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
    def __init__(self, root: str | Path | None = None, *, approval_timeout_s: float = 600.0):
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
        self.gates: dict[str, Gate] = {}
        self._lock = threading.Lock()

    # -- API --------------------------------------------------------------- #
    def submit(self, case_id: str, tenant: str = "demo") -> dict:
        if case_id not in self.workflow.cases:
            raise ValueError(f"未知案例：{case_id}")
        run = self.store.create_run(case_id, tenant)
        gate = Gate()
        with self._lock:
            self.gates[run.id] = gate
        self.store.append_event(run.id, "run.queued", {"case_id": case_id, "tenant": tenant})
        threading.Thread(target=self._run, args=(run.id, case_id, gate), daemon=True).start()
        return {"run_id": run.id, "status": "running", "events_url": f"/v1/assessments/{run.id}/events"}

    def _run(self, run_id: str, case_id: str, gate: Gate) -> None:
        try:
            self.store.append_event(run_id, "run.started", {"case_id": case_id})
            self.workflow.run(run_id, case_id, wait_approval=lambda _rid: gate.wait(self.approval_timeout_s))
        except Exception as exc:  # noqa: BLE001 —— 失败要落事件
            self.store.set_status(run_id, "failed", error=f"{type(exc).__name__}: {exc}")
            self.store.append_event(run_id, "assessment.failed", {"error": str(exc)})
        finally:
            # 超时导致的拒绝：顺手把仍挂着的审批记录结掉
            run = self.store.get_run(run_id)
            pending = self.store.pending_approval(run_id)
            if pending and run and run.status in {"rejected", "failed"}:
                self.store.resolve_approval(pending["id"], False, by="timeout", comment="approval timed out")
            with self._lock:
                self.gates.pop(run_id, None)

    def approve(self, run_id: str, approved: bool, by: str, comment: str = "") -> dict:
        pending = self.store.pending_approval(run_id)
        if not pending:
            raise ValueError("没有待审批的请求")
        resolved = self.store.resolve_approval(pending["id"], approved, by, comment)
        if resolved is None:
            raise ValueError("审批已被处理")
        with self._lock:
            gate = self.gates.get(run_id)
        if gate is None:
            raise ValueError("审批闸门不存在（可能已超时）")
        gate.approved = approved
        gate.by = by
        gate.comment = comment
        gate.event.set()
        return resolved

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
        rows = []
        for run in self._all_runs():
            pending = self.store.pending_approval(run["id"])
            if pending:
                rows.append(pending)
        return rows

    def _all_runs(self) -> list[dict]:
        cursor = self.store._conn.execute("SELECT id FROM runs ORDER BY created_at DESC LIMIT 100")
        return [{"id": row[0]} for row in cursor.fetchall()]


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def make_server(orchestrator: Orchestrator, host: str = "127.0.0.1", port: int = 8081) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # noqa: D401
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
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": f"internal: {type(exc).__name__}"})

        def _route_get(self) -> None:
            url = urlsplit(self.path)
            path, query = url.path, parse_qs(url.query)
            if path == "/healthz":
                return self._json(200, {"ok": True, "runs": len(orchestrator._all_runs())})
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
                threading.Event().wait(0.05)

    return ThreadingHTTPServer((host, port), Handler)
