"""记忆：SOUL / MEMORY / USER 三个 Markdown 文件 + 冻结快照。

这是 Hermes 那条「closed learning loop」里最便宜的一环：
不需要向量库，只要三个可读、可 diff、可被 Agent 自己编辑的文件。

**冻结语义**（照抄 Hermes 的产品决策）：system 提示在会话开始时读一次快照，
会话中 `memory` 工具改了磁盘文件，但不改本会话已发送的 system。
好处是 prompt 前缀字节稳定、缓存可用、行为可预期；代价是「刚记住的事」
要下一个会话才进提示 —— 实验中会专门验证这个边界。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .tools import ToolContext, ToolRegistry
from .util import ensure_dir, read_text, truncate_middle, write_text

KIND_LABEL = {"soul": "SOUL.md", "memory": "MEMORY.md", "user": "USER.md"}

DEFAULTS = {
    "soul": "# SOUL\n\n你是 mini-harness：一个谨慎、可解释、先验证再交付的工程型 Agent。\n",
    "memory": "# MEMORY\n\n<!-- 长期事实与项目知识。每条一行，用 `- ` 开头。 -->\n",
    "user": "# USER\n\n<!-- 用户偏好：语言、工具习惯、代码风格、禁忌。 -->\n",
}


@dataclass
class MemoryStore:
    home: Path
    max_chars: int = 8_000
    snapshot: dict[str, str] = field(default_factory=dict, init=False)

    @property
    def memory_file(self) -> Path:
        return self.home / "MEMORY.md"

    @property
    def user_file(self) -> Path:
        return self.home / "USER.md"

    @property
    def soul_file(self) -> Path:
        return self.home / "SOUL.md"

    def _path(self, kind: str) -> Path:
        mapping = {"soul": self.soul_file, "memory": self.memory_file, "user": self.user_file}
        if kind not in mapping:
            raise ValueError(f"未知记忆文件 {kind!r}，可选: {', '.join(mapping)}")
        return mapping[kind]

    def ensure_files(self) -> None:
        ensure_dir(self.home)
        for kind, text in DEFAULTS.items():
            path = self._path(kind)
            if not path.exists():
                write_text(path, text)

    # -- 冻结快照 ---------------------------------------------------------- #
    def freeze(self) -> dict[str, str]:
        """会话开始时调用一次；之后整个会话都读这份内存副本。"""
        self.ensure_files()
        self.snapshot = {kind: read_text(self._path(kind)) for kind in KIND_LABEL}
        return self.snapshot

    def frozen(self) -> dict[str, str]:
        if not self.snapshot:
            return self.freeze()
        return self.snapshot

    def as_prompt(self, max_chars_each: int = 3_000) -> str:
        snap = self.frozen()
        blocks = []
        for kind in ("soul", "memory", "user"):
            body = truncate_middle(strip_heading(snap.get(kind, "")), max_chars_each).strip()
            if body:
                blocks.append(f"### {KIND_LABEL[kind]}\n{body}")
        return "\n\n".join(blocks)

    # -- 写入 -------------------------------------------------------------- #
    def append(self, kind: str, text: str) -> str:
        path = self._path(kind)
        self.ensure_files()
        current = read_text(path)
        line = text.strip()
        if not line:
            raise ValueError("text 不能为空")
        if not line.startswith("- "):
            line = f"- {line}"
        if line in current.splitlines():
            return f"{KIND_LABEL[kind]} 已包含该条，未重复写入"
        updated = current.rstrip() + "\n" + line + "\n"
        if len(updated) > self.max_chars:
            raise ValueError(
                f"{KIND_LABEL[kind]} 将超过 {self.max_chars} 字符上限；请先 consolidate（合并/删除旧条目）再写"
            )
        write_text(path, updated)
        return f"已写入 {KIND_LABEL[kind]}（{len(updated)} 字符）"

    def replace_line(self, kind: str, old: str, new: str) -> str:
        path = self._path(kind)
        current = read_text(path)
        if old not in current:
            raise ValueError(f"{KIND_LABEL[kind]} 中找不到要替换的内容：{old[:60]!r}")
        write_text(path, current.replace(old, new, 1))
        return f"已更新 {KIND_LABEL[kind]}"

    def remove_line(self, kind: str, text: str) -> str:
        path = self._path(kind)
        lines = read_text(path).splitlines()
        kept = [ln for ln in lines if text not in ln]
        if len(kept) == len(lines):
            raise ValueError(f"{KIND_LABEL[kind]} 中没有匹配 {text[:40]!r} 的条目")
        write_text(path, "\n".join(kept).rstrip() + "\n")
        return f"已删除 {len(lines) - len(kept)} 条"


def strip_heading(markdown: str) -> str:
    lines = [ln for ln in markdown.splitlines() if not ln.strip().startswith("#") and "<!--" not in ln]
    return "\n".join(lines).strip()


# --------------------------------------------------------------------------- #
# 给 Agent 用的记忆工具
# --------------------------------------------------------------------------- #
def register_memory_tools(registry: ToolRegistry, store: MemoryStore) -> None:
    @registry.tool(
        name="memory",
        description=(
            "读写长期记忆文件。op=add 追加一条事实/偏好，replace 替换，remove 删除。"
            "file 可选 memory（项目与事实）或 user（用户偏好）。"
            "注意：写入只落盘，本会话的 system 提示是启动时快照，不会即刻改变。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["add", "replace", "remove"]},
                "file": {"type": "string", "enum": ["memory", "user"], "default": "memory"},
                "text": {"type": "string", "description": "要写入/删除的文本"},
                "old": {"type": "string", "description": "op=replace 时被替换的原文"},
            },
            "required": ["op", "text"],
            "additionalProperties": False,
        },
        group="memory",
    )
    def memory_tool(args: dict, ctx: ToolContext) -> str:
        op = str(args.get("op") or "add")
        kind = str(args.get("file") or "memory")
        text = str(args.get("text") or "")
        if op == "add":
            result = store.append(kind, text)
        elif op == "replace":
            result = store.replace_line(kind, str(args.get("old") or ""), text)
        elif op == "remove":
            result = store.remove_line(kind, text)
        else:
            raise ValueError(f"未知 op: {op}")
        return result + "；本会话 system 提示仍是启动时快照，新内容从下一个会话生效。"
