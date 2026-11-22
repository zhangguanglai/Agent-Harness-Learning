"""会话存储：只追加事件，并从中投影出消息列表。

两个实现：
- `InMemoryStore`：测试与 bench 用；
- `SQLiteStore`：单机持久化，重启后可恢复（生产换 Postgres 同接口）。

投影规则（`project_messages`）只认三类事件：
`run_started`（用户任务）、`model_response`（助手消息 + 工具调用）、`tool_result`（工具结果）。
新增事件类型不需要改投影，除非它改变对话语义。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Iterable, Protocol

from mini_harness.types import ToolCall, assistant_message, tool_message, user_message

from .events import Event


class SessionStore(Protocol):
    def append(self, event: Event) -> Event: ...
    def load_events(self, session_id: str, run_id: str | None = None) -> list[Event]: ...
    def list_runs(self, session_id: str) -> list[str]: ...
    def close(self) -> None: ...


class InMemoryStore:
    def __init__(self) -> None:
        self._events: list[Event] = []
        self._lock = threading.Lock()

    def append(self, event: Event) -> Event:
        with self._lock:
            stored = Event(
                seq=len(self._events) + 1,
                run_id=event.run_id,
                session_id=event.session_id,
                kind=event.kind,
                ts=event.ts,
                data=event.data,
            )
            self._events.append(stored)
            return stored

    def load_events(self, session_id: str, run_id: str | None = None) -> list[Event]:
        with self._lock:
            return [e for e in self._events if e.session_id == session_id and (run_id is None or e.run_id == run_id)]

    def list_runs(self, session_id: str) -> list[str]:
        seen: list[str] = []
        for event in self.load_events(session_id):
            if event.run_id not in seen:
                seen.append(event.run_id)
        return seen

    def close(self) -> None:
        return None


class SQLiteStore:
    """单文件事件存储；每次 append 一个事务，保证「写入即持久」。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                ts REAL NOT NULL,
                data TEXT NOT NULL
            )
            """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, seq)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, seq)")
        self._conn.commit()

    def append(self, event: Event) -> Event:
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO events (run_id, session_id, kind, ts, data) VALUES (?, ?, ?, ?, ?)",
                (event.run_id, event.session_id, event.kind, event.ts, json.dumps(event.data, ensure_ascii=False, default=str)),
            )
            self._conn.commit()
            return Event(
                seq=int(cursor.lastrowid),
                run_id=event.run_id,
                session_id=event.session_id,
                kind=event.kind,
                ts=event.ts,
                data=event.data,
            )

    def load_events(self, session_id: str, run_id: str | None = None) -> list[Event]:
        sql = "SELECT seq, run_id, session_id, kind, ts, data FROM events WHERE session_id = ?"
        params: list[object] = [session_id]
        if run_id is not None:
            sql += " AND run_id = ?"
            params.append(run_id)
        sql += " ORDER BY seq"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            Event(seq=row[0], run_id=row[1], session_id=row[2], kind=row[3], ts=row[4], data=json.loads(row[5]))
            for row in rows
        ]

    def list_runs(self, session_id: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT run_id FROM events WHERE session_id = ? ORDER BY MIN(seq)", (session_id,)
            ).fetchall()
        return [row[0] for row in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def project_messages(events: Iterable[Event], *, exclude_run: str | None = None) -> list[dict]:
    """把事件投影成 OpenAI 风格消息列表，用于断点续跑 / fork / 审计回放。"""
    messages: list[dict] = []
    for event in events:
        if exclude_run is not None and event.run_id == exclude_run:
            continue
        if event.kind == "run_started":
            task = str(event.data.get("task") or "").strip()
            if task:
                messages.append(user_message(task))
        elif event.kind == "model_response":
            calls = [
                ToolCall(id=str(c.get("id") or f"call_{i}"), name=str(c.get("name")), arguments=dict(c.get("arguments") or {}))
                for i, c in enumerate(event.data.get("tool_calls") or [])
            ]
            messages.append(assistant_message(str(event.data.get("text") or ""), calls))
        elif event.kind == "tool_result":
            content = event.data.get("content")
            if content is None:  # 兼容旧事件
                content = str(event.data.get("output") or "")
            messages.append(tool_message(str(event.data.get("call_id")), str(event.data.get("tool")), str(content)))
    return messages


def summarize_events(events: Iterable[Event]) -> dict:
    """给运维/评测用的轻量统计。"""
    counts: dict[str, int] = {}
    tool_calls = 0
    denials = 0
    tokens_in = tokens_out = 0
    for event in events:
        counts[event.kind] = counts.get(event.kind, 0) + 1
        if event.kind == "tool_call":
            tool_calls += 1
        elif event.kind == "permission_decision" and event.data.get("action") == "deny":
            denials += 1
        elif event.kind == "model_response":
            usage = event.data.get("usage") or {}
            tokens_in += int(usage.get("input_tokens") or 0)
            tokens_out += int(usage.get("output_tokens") or 0)
    return {
        "counts": counts,
        "tool_calls": tool_calls,
        "denials": denials,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
    }
