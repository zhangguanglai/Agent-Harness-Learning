"""上下文管理：预算、工具输出裁剪、历史压缩。

Agent 变「笨」通常不是模型退化，而是上下文被稀释或撑满。
这里实现站点里反复强调的三件事：

1. 单条工具输出要**头尾保留式截断**（结论在开头，报错在结尾）；
2. 接近预算时**折叠中段**，保留系统提示、原始任务和最近若干轮；
3. 压缩后做**配对清洗**，不能留下没有调用方的 tool 消息（provider 会拒收）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .util import estimate_messages_tokens, truncate_middle


@dataclass
class CompactionReport:
    before_tokens: int
    after_tokens: int
    dropped_messages: int
    strategy: str

    def to_dict(self) -> dict:
        return {
            "before_tokens": self.before_tokens,
            "after_tokens": self.after_tokens,
            "dropped_messages": self.dropped_messages,
            "strategy": self.strategy,
        }


class ContextManager:
    def __init__(
        self,
        max_context_tokens: int = 8_000,
        compact_at: float = 0.6,
        keep_tail: int = 8,
        max_tool_chars: int = 4_000,
        summarizer: Callable[[list[dict]], str] | None = None,
    ):
        self.max_context_tokens = max_context_tokens
        self.compact_at = compact_at
        self.keep_tail = keep_tail
        self.max_tool_chars = max_tool_chars
        self.summarizer = summarizer

    # -- 预算 -------------------------------------------------------------- #
    def used(self, messages: list[dict]) -> int:
        return estimate_messages_tokens(messages)

    def usage_ratio(self, messages: list[dict]) -> float:
        return self.used(messages) / max(1, self.max_context_tokens)

    def needs_compaction(self, messages: list[dict]) -> bool:
        return self.usage_ratio(messages) >= self.compact_at

    # -- 裁剪 -------------------------------------------------------------- #
    def trim_tool_outputs(self, messages: list[dict]) -> list[dict]:
        """就地生成一份副本，把过长的工具输出截断。"""
        out = []
        for msg in messages:
            if msg.get("role") == "tool" and isinstance(msg.get("content"), str):
                content = msg["content"]
                if len(content) > self.max_tool_chars:
                    msg = {**msg, "content": truncate_middle(content, self.max_tool_chars)}
            out.append(msg)
        return out

    def compact(self, messages: list[dict]) -> tuple[list[dict], CompactionReport | None]:
        """折叠中段历史。返回 (新消息列表, 报告)；无需压缩时报告为 None。"""
        if len(messages) <= self.keep_tail + 2:
            return messages, None

        before = self.used(messages)
        system = messages[0] if messages and messages[0].get("role") == "system" else None
        offset = 1 if system else 0
        rest = messages[offset:]

        # 保留原始任务（第一条 user），它定义了「什么算完成」
        head_len = 0
        for i, msg in enumerate(rest[:2]):
            if msg.get("role") == "user":
                head_len = i + 1
                break

        keep_floor = head_len + self.keep_tail
        if len(rest) <= keep_floor:
            return messages, None

        head = rest[:head_len]
        middle = rest[head_len : len(rest) - self.keep_tail]
        tail = rest[len(rest) - self.keep_tail :]

        summary = self._summarize(middle)
        folded = [{"role": "assistant", "content": f"[上下文压缩] {summary}"}]

        new_messages = ([system] if system else []) + head + folded + self._sanitize_tail(tail)
        after = self.used(new_messages)
        return new_messages, CompactionReport(
            before_tokens=before,
            after_tokens=after,
            dropped_messages=len(middle),
            strategy=f"折叠中段 {len(middle)} 条 / 保留尾 {len(tail)} 条",
        )

    # -- 内部 -------------------------------------------------------------- #
    def _summarize(self, middle: list[dict]) -> str:
        if self.summarizer:
            try:
                return self.summarizer(middle)
            except Exception:  # noqa: BLE001 —— 摘要失败不能拖垮主循环
                pass
        roles: dict[str, int] = {}
        tools: list[str] = []
        for msg in middle:
            role = msg.get("role", "?")
            roles[role] = roles.get(role, 0) + 1
            if role == "tool" and msg.get("name"):
                tools.append(str(msg["name"]))
        tool_desc = "、".join(sorted(set(tools))) or "无"
        last_text = ""
        for msg in reversed(middle):
            if msg.get("role") == "assistant" and msg.get("content"):
                last_text = str(msg["content"])[:160]
                break
        parts = [
            f"已折叠 {len(middle)} 条较早消息（" + "、".join(f"{k} {v}" for k, v in roles.items()) + "）。",
            f"期间调用的工具：{tool_desc}。",
        ]
        if last_text:
            parts.append(f"折叠前最后的模型说明：{last_text}")
        parts.append("如需细节，可重新读取相关文件或重跑命令；不要凭记忆编造被折叠的内容。")
        return " ".join(parts)

    @staticmethod
    def _sanitize_tail(tail: list[dict]) -> list[dict]:
        """压缩边界不能留下无主的 tool 消息，否则真实 provider 会 400。"""
        out = list(tail)
        while out and out[0].get("role") == "tool":
            out.pop(0)
        return out
