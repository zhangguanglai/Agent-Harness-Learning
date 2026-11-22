"""运行时：把「一次 Agent 循环」变成「一个可管理、可观测、可恢复的 Run」。

与 mini_harness 的分工：
- `mini_harness` 负责「怎么想、怎么调工具」；
- `runtime` 负责「谁来跑、跑到哪、能不能停、出事怎么查」。

四个生产语义都在这里落地：
1. `RunHandle`：异步提交、状态机、取消；
2. 事件持久化：每个阶段追加事件，重启后可重建；
3. 审批：`waiting_approval` 是状态，不是阻塞回调；
4. 生命周期：worker 池、超时、失败归档。
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from mini_harness.llm import DemoLLM, LLM
from mini_harness.loop import Agent, AgentConfig, RunResult
from mini_harness.policy import Mode
from mini_harness.sandbox import LocalSandbox, Sandbox
from mini_harness.util import new_id

from .approvals import ApprovalBroker, ApprovalRequest, AutoApprover
from .events import Event, new_event
from .gateway import ToolGateway
from .policy import PolicyContext, TenantPermissionPolicy
from .store import SessionStore, project_messages


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL = {RunStatus.DONE, RunStatus.FAILED, RunStatus.CANCELLED}


@dataclass
class RunRequest:
    task: str
    session_id: str
    tenant: str = "default"
    user: str = "anonymous"
    roles: tuple[str, ...] = ("operator",)
    workspace: Path = field(default_factory=Path.cwd)
    mode: Mode = Mode.DEFAULT
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    ask: tuple[str, ...] = ()
    max_turns: int = 16
    token_budget: int = 120_000
    max_context_tokens: int = 8_000
    compact_at: float = 0.6
    approval_timeout_s: float = 300.0
    system_extra: str = ""
    memory_home: Path | None = None
    skills_root: Path | None = None
    #: 离线 bench 用脚本模型；生产传真实 LLM 客户端
    llm: LLM | None = None


class RunHandle:
    def __init__(self, runtime: "Runtime", request: RunRequest):
        self.runtime = runtime
        self.request = request
        self.run_id = new_id("run")
        self.session_id = request.session_id
        self.cancel_event = threading.Event()
        self.cancel_reason = ""
        self.result: RunResult | None = None
        self.error: str = ""
        self.gateway: ToolGateway | None = None
        self._status = RunStatus.QUEUED
        self._lock = threading.Lock()
        self._done = threading.Event()

    # -- 状态 -------------------------------------------------------------- #
    @property
    def status(self) -> RunStatus:
        with self._lock:
            return self._status

    def set_status(self, status: RunStatus) -> None:
        with self._lock:
            self._status = status
        if status in TERMINAL:
            self._done.set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout)

    @property
    def finished(self) -> bool:
        return self._done.is_set()

    # -- 控制 -------------------------------------------------------------- #
    def cancel(self, reason: str = "user_cancel") -> None:
        self.runtime.cancel(self.run_id, reason)

    def events(self) -> list[Event]:
        return self.runtime.events(self.run_id)


class Runtime:
    def __init__(
        self,
        store: SessionStore,
        *,
        sandbox: Sandbox | None = None,
        workers: int = 2,
        broker: ApprovalBroker | None = None,
        on_event: Callable[[Event], None] | None = None,
    ):
        self.store = store
        self.sandbox = sandbox or LocalSandbox()
        self.broker = broker or ApprovalBroker()
        self.on_event = on_event
        self._pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="harness-worker")
        self._handles: dict[str, RunHandle] = {}
        self._lock = threading.Lock()

    # -- 对外 API ---------------------------------------------------------- #
    def submit(self, request: RunRequest) -> RunHandle:
        handle = RunHandle(self, request)
        with self._lock:
            self._handles[handle.run_id] = handle
        self._append(handle, "run_queued", task=request.task, tenant=request.tenant, user=request.user)
        self._pool.submit(self._execute, handle)
        return handle

    def cancel(self, run_id: str, reason: str = "user_cancel") -> bool:
        with self._lock:
            handle = self._handles.get(run_id)
        if handle is None or handle.finished:
            return False
        handle.cancel_reason = reason
        handle.cancel_event.set()
        return True

    def events(self, run_id: str) -> list[Event]:
        with self._lock:
            handle = self._handles.get(run_id)
        if handle is None:
            return []
        return self.store.load_events(handle.session_id, run_id)

    def session_events(self, session_id: str) -> list[Event]:
        return self.store.load_events(session_id)

    def history(self, session_id: str, *, exclude_run: str | None = None) -> list[dict]:
        """断点续跑的入口：从事件重建历史消息。"""
        return project_messages(self.store.load_events(session_id), exclude_run=exclude_run)

    def wait(self, run_id: str, timeout: float | None = None) -> RunHandle | None:
        with self._lock:
            handle = self._handles.get(run_id)
        if handle is None:
            return None
        handle.wait(timeout)
        return handle

    # -- 查询（供 API / 运维使用） ------------------------------------------ #
    def get(self, run_id: str) -> RunHandle | None:
        with self._lock:
            return self._handles.get(run_id)

    def handles(self) -> list[RunHandle]:
        with self._lock:
            return list(self._handles.values())

    def active_runs(self, tenant: str | None = None) -> list[RunHandle]:
        return [
            h
            for h in self.handles()
            if not h.finished and (tenant is None or h.request.tenant == tenant)
        ]

    def close(self) -> None:
        self._pool.shutdown(wait=True)

    # -- 执行 -------------------------------------------------------------- #
    def _execute(self, handle: RunHandle) -> None:
        request = handle.request
        if handle.cancel_event.is_set():
            handle.set_status(RunStatus.CANCELLED)
            self._append(handle, "run_cancelled", reason=handle.cancel_reason or "cancelled_before_start", turns=0)
            return

        handle.set_status(RunStatus.RUNNING)
        self._append(
            handle,
            "run_started",
            task=request.task,
            tenant=request.tenant,
            user=request.user,
            mode=request.mode.value,
            workspace=str(request.workspace),
        )
        started = time.perf_counter()
        try:
            agent = self._build_agent(handle)
            history = project_messages(self.store.load_events(request.session_id), exclude_run=handle.run_id)
            result = agent.run(request.task, history=history)
            handle.result = result
            if result.stopped == "interrupted":
                handle.set_status(RunStatus.CANCELLED)
                self._append(
                    handle,
                    "run_cancelled",
                    reason=handle.cancel_reason or "cancelled",
                    turns=result.turns,
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                )
            else:
                ok = result.stopped == "final"
                handle.set_status(RunStatus.DONE if ok else RunStatus.FAILED)
                self._append(
                    handle,
                    "run_finished",
                    stopped=result.stopped,
                    text=result.text,
                    turns=result.turns,
                    usage={
                        "input_tokens": result.usage.input_tokens,
                        "output_tokens": result.usage.output_tokens,
                        "total": result.usage.total,
                    },
                    denials=len(result.denials),
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                )
        except Exception as exc:  # noqa: BLE001 —— 失败也要落事件，不能静默
            handle.error = f"{type(exc).__name__}: {exc}"
            handle.set_status(RunStatus.FAILED)
            self._append(handle, "run_failed", error=handle.error, duration_ms=round((time.perf_counter() - started) * 1000, 1))

    def _build_agent(self, handle: RunHandle) -> Agent:
        request = handle.request
        ctx = PolicyContext(
            tenant=request.tenant,
            user=request.user,
            roles=request.roles,
            session_id=request.session_id,
            run_id=handle.run_id,
        )
        policy = TenantPermissionPolicy(
            ctx=ctx,
            mode=request.mode,
            allow=request.allow,
            deny=request.deny,
            ask=request.ask,
            sandbox_root=Path(request.workspace),
        )
        config = AgentConfig(
            workspace=Path(request.workspace),
            mode=request.mode,
            allow=request.allow,
            deny=request.deny,
            ask=request.ask,
            max_turns=request.max_turns,
            token_budget=request.token_budget,
            max_context_tokens=request.max_context_tokens,
            compact_at=request.compact_at,
            memory_home=request.memory_home,
            skills_root=request.skills_root,
            system_extra=(request.system_extra + f"\n当前主体：{ctx.describe()}").strip(),
        )
        agent = Agent(
            request.llm or DemoLLM(),
            config,
            approver=self._make_approver(handle),
            on_event=lambda event: self._on_agent_event(handle, event),
            cancel_event=handle.cancel_event,
            sandbox=self.sandbox,
            policy=policy,
        )
        gateway = ToolGateway(agent.registry)
        agent.tool_executor = gateway.invoke
        handle.gateway = gateway
        return agent

    # -- 审批 -------------------------------------------------------------- #
    def _make_approver(self, handle: RunHandle):
        approver = AutoApprover(
            self.broker,
            run_id=handle.run_id,
            session_id=handle.session_id,
            timeout_s=handle.request.approval_timeout_s,
            on_request=lambda request: self._on_approval_requested(handle, request),
        )

        def wrapped(call, decision) -> bool:
            approved = approver(call, decision)
            self._on_approval_resolved(handle, approver.last_request, approved)
            handle.set_status(RunStatus.RUNNING)
            return approved

        return wrapped

    def _on_approval_requested(self, handle: RunHandle, request: ApprovalRequest) -> None:
        handle.set_status(RunStatus.WAITING_APPROVAL)
        self._append(
            handle,
            "approval_requested",
            approval_id=request.id,
            tool=request.tool,
            args=request.args,
            reason=request.reason,
        )

    def _on_approval_resolved(self, handle: RunHandle, request: ApprovalRequest | None, approved: bool) -> None:
        self._append(
            handle,
            "approval_resolved",
            approval_id=request.id if request else "",
            approved=approved,
            by=request.resolved_by if request else "",
        )

    # -- 事件桥接 ---------------------------------------------------------- #
    def _on_agent_event(self, handle: RunHandle, event: dict) -> None:
        kind = event.get("type")
        if kind == "turn":
            self._append(handle, "turn_started", turn=event.get("turn"))
        elif kind == "llm_response":
            self._append(
                handle,
                "model_response",
                turn=event.get("turn"),
                text=event.get("text") or "",
                tool_calls=event.get("tool_calls") or [],
                usage={"input_tokens": event.get("input_tokens"), "output_tokens": event.get("output_tokens")},
            )
        elif kind == "permission":
            self._append(
                handle,
                "permission_decision",
                tool=event.get("tool"),
                action=str(event.get("decision", "")).split(" ")[0].lower() or "unknown",
                reason=str(event.get("decision", "")),
            )
        elif kind == "tool_call":
            self._append(handle, "tool_call", tool=event.get("tool"), args=event.get("args"), call_id=event.get("call_id"))
        elif kind == "tool_result":
            self._append(
                handle,
                "tool_result",
                tool=event.get("tool"),
                call_id=event.get("call_id"),
                ok=bool(event.get("ok")),
                denied=bool(event.get("denied")),
                error_code=event.get("error_code") or "",
                content=event.get("content") or "",
                duration_ms=event.get("duration_ms"),
            )
        elif kind == "compaction":
            self._append(handle, "compaction", **(event.get("report") or {}))

    # -- 工具 -------------------------------------------------------------- #
    def _append(self, handle: RunHandle, kind: str, **data) -> Event:
        stored = self.store.append(new_event(handle.run_id, handle.session_id, kind, data))
        if self.on_event:
            try:
                self.on_event(stored)
            except Exception:  # noqa: BLE001
                pass
        return stored
