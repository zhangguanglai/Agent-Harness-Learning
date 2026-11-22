"""引用模型：所有结论必须能落到「文档 + 条款」，否则视为不可信。

这是场景 A（跨境数据出境合规）的底线：合规结论一旦编造条款号，
下游会拿它去做申报，后果比「没答上来」严重得多。

约定：citation id 形如 `REG-PIPL#第38条`，由 `CitationIndex` 统一登记与校验。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Citation:
    doc_id: str
    clause: str

    @property
    def cid(self) -> str:
        return f"{self.doc_id}#{self.clause}"

    @classmethod
    def parse(cls, cid: str) -> "Citation":
        doc_id, sep, clause = cid.partition("#")
        if not sep or not doc_id or not clause:
            raise ValueError(f"非法引用 id: {cid!r}（应形如 DOC#第X条）")
        return cls(doc_id=doc_id, clause=clause)


@dataclass
class CitationIndex:
    entries: dict[str, str] = field(default_factory=dict)  # cid -> 原文
    docs: dict[str, list[str]] = field(default_factory=dict)  # doc_id -> [clause]

    def add(self, doc_id: str, clause: str, text: str) -> str:
        citation = Citation(doc_id=doc_id, clause=clause)
        self.entries[citation.cid] = text.strip()
        self.docs.setdefault(doc_id, []).append(clause)
        return citation.cid

    def get(self, cid: str) -> str | None:
        return self.entries.get(cid)

    def validate(self, cids: list[str]) -> tuple[bool, list[str]]:
        missing = [cid for cid in cids if cid not in self.entries]
        return (not missing, missing)

    def resolve_quotes(self, cids: list[str], limit: int = 160) -> list[dict]:
        """把引用展开成可展示的证据（原文截断）。"""
        out = []
        for cid in cids:
            text = self.entries.get(cid)
            if text is None:
                out.append({"cid": cid, "quote": None, "valid": False})
                continue
            quote = text if len(text) <= limit else text[: limit - 1] + "…"
            out.append({"cid": cid, "quote": quote, "valid": True})
        return out
