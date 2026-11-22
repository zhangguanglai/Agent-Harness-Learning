"""审批：`waiting_approval` 是一等状态，而不是一个阻塞回调。

生产要求：
- 审批请求可持久化、可查询、可超时；
- 批准人（谁批的）要留痕，供审计；
- 运行时在等待期间不能假装「正在跑」，否则监控与配额都会失真。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from mini_harness.util import new_id


@dataclass
class ApprovalRequest:
    id: str
    run_id: str
    session_id: str
    tool: str
    args: dict
    reason: str
    created_at: float = field(default_factory=time.time)
    timeout_s: float = 300.0
    status: str = "pending"  # pending | approved | denied | expired
    resolved_by: str = ""
    resolved_at: float = 0.0

    @property
    def settled(self) -> bool:
        return self.status != "pending"


class ApprovalBroker:
    """线程安全的审批登记处。

    `auto_approve=True` 用于 bench / CI；生产里由外部 API 调 `resolve()`。
    """

    def __init__(self, auto_approve: bool = False):
        self.auto_approve = auto_approve
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._requests: dict[str, ApprovalRequest] = {}
        self._order: list[str] = []

    def request(
        self,
        *,
        run_id: str,
        session_id: str,
        tool: str,
        args: dict,
        reason: str,
        timeout_s: float = 300.0,
    ) -> ApprovalRequest:
        request = ApprovalRequest(
            id=new_id("approval"),
            run_id=run_id,
            session_id=session_id,
            tool=tool,
            args=dict(args),
            reason=reason,
            timeout_s=timeout_s,
        )
        with self._cond:
            self._requests[request.id] = request
            self._order.append(request.id)
            if self.auto_approve:
                request.status = "approved"
                request.resolved_by = "auto"
                request.resolved_at = time.time()
        return request

    def resolve(self, request_id: str, approved: bool, by: str = "user") -> bool:
        with self._cond:
            request = self._requests.get(request_id)
            if request is None or request.settled:
                return False
            request.status = "approved" if approved else "denied"
            request.resolved_by = by
            request.resolved_at = time.time()
            self._cond.notify_all()
            return True

    def wait(self, request_id: str, timeout: float | None = None) -> bool:
        """阻塞直到有结论；超时按拒绝处理（fail closed）。"""
        deadline = time.time() + (timeout if timeout is not None else 300.0)
        with self._cond:
            request = self._requests.get(request_id)
            if request is None:
                return False
            while not request.settled:
                remaining = deadline - time.time()
                if remaining <= 0:
                    request.status = "expired"
                    request.resolved_at = time.time()
                    request.resolved_by = "timeout"
                    return False
                self._cond.wait(timeout=min(remaining, 0.2))
            return request.status == "approved"

    def pending(self) -> list[ApprovalRequest]:
        with self._lock:
            return [self._requests[i] for i in self._order if self._requests[i].status == "pending"]

    def get(self, request_id: str) -> ApprovalRequest | None:
        with self._lock:
            return self._requests.get(request_id)

    def history(self) -> list[ApprovalRequest]:
        with self._lock:
            return [self._requests[i] for i in self._order]


class AutoApprover:
    """把 broker 适配成 `Agent.approver` 需要的签名 `(ToolCall, Decision) -> bool`。"""

    def __init__(self, broker: ApprovalBroker, *, run_id: str, session_id: str, timeout_s: float, on_request: Callable[[ApprovalRequest], None]):
        self.broker = broker
        self.run_id = run_id
        self.session_id = session_id
        self.timeout_s = timeout_s
        self.on_request = on_request
        self.last_request: ApprovalRequest | None = None

    def __call__(self, call, decision) -> bool:
        request = self.broker.request(
            run_id=self.run_id,
            session_id=self.session_id,
            tool=call.name,
            args=call.arguments,
            reason=decision.reason,
            timeout_s=self.timeout_s,
        )
        self.last_request = request
        self.on_request(request)
        return self.broker.wait(request.id, timeout=self.timeout_s)
