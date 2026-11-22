"""制度/法规检索（离线词法版）。

生产用混合检索（BM25 + 向量 + 重排）；这里用「关键词命中 + 条款级切分」，
目的不是效果，而是给工具定义与引用校验一个**可复现**的基线。

关键约束：返回的每个结果都带 citation id，模型只能引用检索结果里的 id。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .citations import CitationIndex

CLAUSE_RE = re.compile(r"^##\s*(.+?)\s*$")
DOC_META_RE = re.compile(r"^#\s*(.+?)\s*$")


@dataclass
class Chunk:
    cid: str
    doc_id: str
    clause: str
    title: str
    text: str
    score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "cid": self.cid,
            "doc_id": self.doc_id,
            "clause": self.clause,
            "title": self.title,
            "text": self.text[:400],
            "score": round(self.score, 3),
        }


class KnowledgeBase:
    def __init__(self) -> None:
        self.chunks: list[Chunk] = []
        self.index = CitationIndex()

    @classmethod
    def load(cls, root: str | Path) -> "KnowledgeBase":
        """加载 `root/**/*.md`；文件名去掉 .md 即 doc_id，`##` 标题即条款。"""
        kb = cls()
        root = Path(root)
        for path in sorted(root.rglob("*.md")):
            doc_id = path.stem.upper()
            lines = path.read_text(encoding="utf-8").splitlines()
            title = doc_id
            clause = "前言"
            buffer: list[str] = []

            def flush() -> None:
                nonlocal buffer
                body = "\n".join(buffer).strip()
                if body:
                    cid = kb.index.add(doc_id, clause, body)
                    kb.chunks.append(Chunk(cid=cid, doc_id=doc_id, clause=clause, title=title, text=body))
                buffer = []

            for line in lines:
                meta = DOC_META_RE.match(line)
                if meta and line.startswith("# "):
                    title = meta.group(1)
                    continue
                hit = CLAUSE_RE.match(line)
                if hit:
                    flush()
                    clause = hit.group(1)
                    continue
                buffer.append(line)
            flush()
        return kb

    # -- 检索 -------------------------------------------------------------- #
    def search(self, query: str, top_k: int = 5) -> list[dict]:
        tokens = [t for t in re.split(r"[\s，。；、,;:：()（）]+", query) if len(t) >= 2]
        scored: list[Chunk] = []
        for chunk in self.chunks:
            haystack = f"{chunk.title} {chunk.clause} {chunk.text}"
            score = sum(haystack.count(token) for token in tokens)
            if score > 0:
                scored.append(Chunk(**{**chunk.__dict__, "score": float(score)}))
        scored.sort(key=lambda c: (-c.score, c.cid))
        return [chunk.to_dict() for chunk in scored[:top_k]]

    def clause(self, cid: str) -> str | None:
        return self.index.get(cid)
