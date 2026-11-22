"""事件模型：生产 Harness 的唯一事实来源。

为什么要事件而不是「把 messages 存成 JSON」：
- 可以回放「第 7 轮为什么被拒」；
- 可以从任意事件重建上下文（断点续跑）；
- 审计、计费、评测共用同一条流；
- 压缩、fork、灰度对比都变成「追加事件」而不是「覆盖状态」。

事件一旦写入不可修改；状态是事件的投影。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

#: 事件类型白名单，避免各模块随手写字符串导致下游无法消费。
EVENT_KINDS = frozenset(
    {
        "run_queued",
        "run_started",
        "turn_started",
        "model_response",
        "permission_decision",
        "approval_requested",
        "approval_resolved",
        "tool_call",
        "tool_result",
        "compaction",
        "run_finished",
        "run_cancelled",
        "run_failed",
    }
)


@dataclass(frozen=True)
class Event:
    seq: int
    run_id: str
    session_id: str
    kind: str
    ts: float
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in EVENT_KINDS:
            raise ValueError(f"未知事件类型: {self.kind}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "kind": self.kind,
            "ts": self.ts,
            "data": self.data,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Event":
        return cls(
            seq=int(payload["seq"]),
            run_id=str(payload["run_id"]),
            session_id=str(payload["session_id"]),
            kind=str(payload["kind"]),
            ts=float(payload["ts"]),
            data=dict(payload.get("data") or {}),
        )


def new_event(run_id: str, session_id: str, kind: str, data: dict[str, Any] | None = None) -> Event:
    """构造一个尚未分配 seq 的事件（seq=0，由存储层分配）。"""
    return Event(seq=0, run_id=run_id, session_id=session_id, kind=kind, ts=time.time(), data=dict(data or {}))
