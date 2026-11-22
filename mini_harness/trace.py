"""可观测性：事件轨迹（JSONL）+ 用量账本。

为什么要先写这个再写循环：Agent 的失败大多是「过程不可见」——
不知道第几轮、哪个工具、哪条权限规则拦住了。轨迹是调试、计费、
以及后面压缩策略调参的唯一事实来源。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .util import jsonl_append, new_id, now_iso


@dataclass
class Tracer:
    path: Path | None = None
    session_id: str = field(default_factory=lambda: new_id("session"))
    echo: bool = False
    events: list[dict] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)

    def emit(self, kind: str, **payload: Any) -> dict:
        record = {
            "ts": now_iso(),
            "session": self.session_id,
            "seq": len(self.events) + 1,
            "kind": kind,
            **payload,
        }
        self.events.append(record)
        # 只从模型调用事件累计 token；session_end 也带 usage 字段，不能重复计
        if kind == "llm_response":
            self.totals["input_tokens"] = self.totals.get("input_tokens", 0) + int(payload.get("input_tokens") or 0)
            self.totals["output_tokens"] = self.totals.get("output_tokens", 0) + int(payload.get("output_tokens") or 0)
        if self.path:
            jsonl_append(self.path, record)
        if self.echo:
            brief = json.dumps({k: v for k, v in payload.items() if k in ("turn", "tool", "action", "reason")}, ensure_ascii=False)
            print(f"[trace] {kind:<18} {brief}")
        return record

    # -- 便捷方法 ---------------------------------------------------------- #
    def of_kind(self, kind: str) -> list[dict]:
        return [e for e in self.events if e.get("kind") == kind]

    def tool_calls(self) -> list[dict]:
        return self.of_kind("tool_call")

    def denials(self) -> list[dict]:
        return [e for e in self.of_kind("permission") if e.get("action") == "deny"]

    @property
    def usage(self) -> dict[str, int]:
        return {
            "input_tokens": self.totals.get("input_tokens", 0),
            "output_tokens": self.totals.get("output_tokens", 0),
            "total": self.totals.get("input_tokens", 0) + self.totals.get("output_tokens", 0),
        }

    def summary(self) -> dict:
        return {
            "session": self.session_id,
            "events": len(self.events),
            "tool_calls": len(self.tool_calls()),
            "denials": len(self.denials()),
            "usage": self.usage,
            "compactions": len(self.of_kind("compaction")),
            "path": str(self.path) if self.path else None,
        }

    def print_summary(self) -> None:
        s = self.summary()
        usage = s["usage"]
        print(
            "\n— 运行小结 —\n"
            f"事件 {s['events']} 条 · 工具调用 {s['tool_calls']} 次 · 拒绝 {s['denials']} 次 · 压缩 {s['compactions']} 次\n"
            f"token：输入 {usage['input_tokens']} / 输出 {usage['output_tokens']} / 合计 {usage['total']}\n"
            f"轨迹文件：{s['path'] or '（未落盘）'}"
        )


def load_trace(path: Path) -> list[dict]:
    """读取 JSONL 轨迹，供实验与复盘脚本使用。"""
    records = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records
