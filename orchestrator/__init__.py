"""编排层：把「一次出境评估」跑成可审批的 run。

模块：
- `store`：SQLite（runs/events/approvals/artifacts）
- `workflow`：抽取 → 判定 → 引用校验 → 起草 → 审批 → 定稿
- `worker_client`：驱动 spike/dsh-worker（HTTP + SSE）
- `service`：HTTP + SSE + 审批闸门 + worker 集成

对应 [docs/18](../docs/18-s1-orchestration.md)。
"""

from .service import Gate, Orchestrator, make_server
from .store import Store
from .worker_client import WorkerClient, WorkerError
from .workflow import AssessmentWorkflow, render_report

__all__ = [
    "Orchestrator",
    "Gate",
    "make_server",
    "Store",
    "AssessmentWorkflow",
    "render_report",
    "WorkerClient",
    "WorkerError",
]
