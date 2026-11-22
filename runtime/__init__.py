"""runtime：生产形态的 Harness 外壳（Phase 0 骨架）。

与 `mini_harness`（教学内核）的分工：
- `mini_harness`：循环、工具协议、权限语义、上下文与记忆；
- `runtime`：异步 Run、事件持久化、工具网关、主体权限、审批状态机。

最小用法：

    from runtime import Runtime, RunRequest, InMemoryStore

    rt = Runtime(InMemoryStore(), broker=ApprovalBroker(auto_approve=True))
    handle = rt.submit(RunRequest(task="...", session_id="s1", workspace=Path(".")))
    handle.wait(30)
    print(handle.status, handle.result.text)
    rt.close()
"""

from .approvals import ApprovalBroker, ApprovalRequest, AutoApprover
from .events import EVENT_KINDS, Event, new_event
from .gateway import ToolGateway, ToolSpec
from .policy import PolicyContext, TenantPermissionPolicy, capabilities_for
from .runtime import Runtime, RunHandle, RunRequest, RunStatus, TERMINAL
from .store import InMemoryStore, SQLiteStore, SessionStore, project_messages, summarize_events

__all__ = [
    "Runtime",
    "RunHandle",
    "RunRequest",
    "RunStatus",
    "TERMINAL",
    "Event",
    "EVENT_KINDS",
    "new_event",
    "SessionStore",
    "InMemoryStore",
    "SQLiteStore",
    "project_messages",
    "summarize_events",
    "ApprovalBroker",
    "ApprovalRequest",
    "AutoApprover",
    "ToolGateway",
    "ToolSpec",
    "PolicyContext",
    "TenantPermissionPolicy",
    "capabilities_for",
]

__version__ = "0.1.0"
