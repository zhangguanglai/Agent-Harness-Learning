"""编排层存储：运行、事件、审批、产物（SQLite，单机可跑）。

设计沿用 docs/15 的三条原则：
1. 事件只追加，状态是投影；
2. 审批是一等状态（waiting_approval），不是阻塞在某个回调里；
3. 产物版本化，审批通过才标 final。

并发：单个连接 + 一把锁。SQLite 的 `check_same_thread=False` 只解决「跨线程可用」，
不解决「并发使用同一游标」，所以每个读写都必须串行。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .artifacts import ArtifactStore, LocalArtifactStore

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, case_id TEXT NOT NULL, tenant TEXT NOT NULL,
  status TEXT NOT NULL, created_at REAL NOT NULL, finished_at REAL,
  decision TEXT, error TEXT, rules_version TEXT NOT NULL DEFAULT 'v1'
);
CREATE TABLE IF NOT EXISTS events (
  run_id TEXT NOT NULL, seq INTEGER NOT NULL, type TEXT NOT NULL,
  ts REAL NOT NULL, payload TEXT NOT NULL,
  PRIMARY KEY (run_id, seq)
);
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, action TEXT NOT NULL,
  status TEXT NOT NULL, requested_at REAL NOT NULL,
  decided_at REAL, decided_by TEXT, comment TEXT
);
CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, version INTEGER NOT NULL,
  path TEXT NOT NULL, sha256 TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL
);
"""


@dataclass
class Run:
    id: str
    case_id: str
    tenant: str
    status: str
    created_at: float
    finished_at: float | None = None
    decision: dict | None = None
    error: str | None = None
    rules_version: str = "v1"


class Store:
    def __init__(self, db_path: str | Path, artifacts: ArtifactStore | str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifact_store = artifacts if hasattr(artifacts, "put") else LocalArtifactStore(artifacts)  # type: ignore[arg-type]
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.executescript(SCHEMA)
        self._ensure_column("runs", "rules_version", "TEXT NOT NULL DEFAULT 'v1'")
        self._conn.commit()

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        existing = {row[1] for row in self._conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    # -- run --------------------------------------------------------------- #
    def create_run(self, case_id: str, tenant: str, rules_version: str = "v1") -> Run:
        run = Run(
            id=f"run_{uuid.uuid4().hex[:12]}",
            case_id=case_id,
            tenant=tenant,
            status="running",
            created_at=time.time(),
            rules_version=rules_version,
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs (id, case_id, tenant, status, created_at, rules_version) VALUES (?, ?, ?, ?, ?, ?)",
                (run.id, run.case_id, run.tenant, run.status, run.created_at, run.rules_version),
            )
            self._conn.commit()
        return run

    def get_run(self, run_id: str) -> Run | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, case_id, tenant, status, created_at, finished_at, decision, error, rules_version FROM runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if not row:
            return None
        return Run(
            id=row[0], case_id=row[1], tenant=row[2], status=row[3], created_at=row[4],
            finished_at=row[5], decision=json.loads(row[6]) if row[6] else None, error=row[7],
            rules_version=row[8] if len(row) > 8 else "v1",
        )

    def set_status(self, run_id: str, status: str, *, decision: dict | None = None, error: str | None = None) -> None:
        finished = time.time() if status in {"completed", "rejected", "failed"} else None
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET status = ?, finished_at = COALESCE(?, finished_at), decision = COALESCE(?, decision), error = COALESCE(?, error) WHERE id = ?",
                (status, finished, json.dumps(decision, ensure_ascii=False) if decision else None, error, run_id),
            )
            self._conn.commit()

    def recent_runs(self, limit: int = 100) -> list[str]:
        with self._lock:
            rows = self._conn.execute("SELECT id FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [row[0] for row in rows]

    # -- events ------------------------------------------------------------ #
    def append_event(self, run_id: str, event_type: str, payload: dict) -> dict:
        with self._lock:
            seq = self._conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE run_id = ?", (run_id,)).fetchone()[0]
            event = {"run_id": run_id, "seq": seq, "type": event_type, "ts": time.time(), "payload": payload}
            self._conn.execute(
                "INSERT INTO events (run_id, seq, type, ts, payload) VALUES (?, ?, ?, ?, ?)",
                (run_id, seq, event_type, event["ts"], json.dumps(payload, ensure_ascii=False)),
            )
            self._conn.commit()
        return event

    def events(self, run_id: str, after: int = 0) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT run_id, seq, type, ts, payload FROM events WHERE run_id = ? AND seq > ? ORDER BY seq",
                (run_id, after),
            ).fetchall()
        return [
            {"run_id": r[0], "seq": r[1], "type": r[2], "ts": r[3], "payload": json.loads(r[4])} for r in rows
        ]

    # -- approvals --------------------------------------------------------- #
    def create_approval(self, run_id: str, action: str) -> dict:
        approval = {"id": f"apr_{uuid.uuid4().hex[:10]}", "run_id": run_id, "action": action, "status": "pending", "requested_at": time.time()}
        with self._lock:
            self._conn.execute(
                "INSERT INTO approvals (id, run_id, action, status, requested_at) VALUES (?, ?, ?, ?, ?)",
                (approval["id"], run_id, action, "pending", approval["requested_at"]),
            )
            self._conn.commit()
        return approval

    def resolve_approval(self, approval_id: str, approved: bool, by: str, comment: str = "") -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT id, run_id, action, status FROM approvals WHERE id = ?", (approval_id,)).fetchone()
            if not row or row[3] != "pending":
                return None
            status = "approved" if approved else "rejected"
            self._conn.execute(
                "UPDATE approvals SET status = ?, decided_at = ?, decided_by = ?, comment = ? WHERE id = ?",
                (status, time.time(), by, comment, approval_id),
            )
            self._conn.commit()
        return {"id": row[0], "run_id": row[1], "action": row[2], "status": status, "by": by, "comment": comment}

    def pending_approval(self, run_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, run_id, action, status FROM approvals WHERE run_id = ? AND status = 'pending' ORDER BY requested_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        return {"id": row[0], "run_id": row[1], "action": row[2], "status": row[3]} if row else None

    # -- artifacts --------------------------------------------------------- #
    def write_artifact(self, run_id: str, filename: str, content: str, *, status: str) -> dict:
        stored = self.artifact_store.put(run_id, filename, content)
        path, digest = stored.path, stored.sha256
        with self._lock:
            version = self._conn.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM artifacts WHERE run_id = ?", (run_id,)).fetchone()[0]
            artifact = {
                "id": f"art_{uuid.uuid4().hex[:10]}",
                "run_id": run_id,
                "version": version,
                "path": str(path),
                "sha256": digest,
                "status": status,
                "created_at": time.time(),
            }
            self._conn.execute(
                "INSERT INTO artifacts (id, run_id, version, path, sha256, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (artifact["id"], run_id, version, artifact["path"], digest, status, artifact["created_at"]),
            )
            self._conn.commit()
        return artifact

    def artifacts(self, run_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, run_id, version, path, sha256, status, created_at FROM artifacts WHERE run_id = ? ORDER BY version",
                (run_id,),
            ).fetchall()
        return [
            {"id": r[0], "run_id": r[1], "version": r[2], "path": r[3], "sha256": r[4], "status": r[5], "created_at": r[6]}
            for r in rows
        ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
