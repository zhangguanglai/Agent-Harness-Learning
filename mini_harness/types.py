"""消息、工具调用、工具结果的最小数据模型。

内部消息格式统一为 **OpenAI 风格 dict**（role/content/tool_calls/tool_call_id），
理由与 Hermes 一致：它是事实上的跨家格式，适配 Anthropic 或 Responses API
只需要在边界处转换，循环体本身保持平台无关。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


# --------------------------------------------------------------------------- #
# 消息构造器
# --------------------------------------------------------------------------- #
def system_message(text: str) -> dict:
    return {"role": "system", "content": text}


def user_message(text: str) -> dict:
    return {"role": "user", "content": text}


def assistant_message(text: str = "", tool_calls: list["ToolCall"] | None = None) -> dict:
    msg: dict[str, Any] = {"role": "assistant", "content": text or ""}
    if tool_calls:
        msg["tool_calls"] = [call.to_openai() for call in tool_calls]
    return msg


def tool_message(call_id: str, name: str, content: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}


# --------------------------------------------------------------------------- #
# 工具调用与结果
# --------------------------------------------------------------------------- #
@dataclass
class ToolCall:
    """模型请求的一次工具调用。arguments 已在边界处反序列化为 dict。"""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    id: str = "call_0"

    @classmethod
    def from_openai(cls, raw: dict) -> "ToolCall":
        fn = raw.get("function") or {}
        raw_args = fn.get("arguments")
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args) if raw_args.strip() else {}
            except json.JSONDecodeError:
                # 坏 JSON 不炸循环：把它塞进一个可解释的字段交给工具层报错
                args = {"__raw__": raw_args, "__parse_error__": "arguments 不是合法 JSON"}
        elif isinstance(raw_args, dict):
            args = raw_args
        else:
            args = {}
        return cls(name=fn.get("name", ""), arguments=args, id=raw.get("id") or "call_0")

    def to_openai(self) -> dict:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }

    def summary(self, limit: int = 120) -> str:
        args = json.dumps(self.arguments, ensure_ascii=False)
        return f"{self.name}({args[:limit]}{'…' if len(args) > limit else ''})"


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens)

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total": self.total,
        }


@dataclass
class LLMResponse:
    """一次模型调用的结果：要么有文字，要么有工具调用，或两者都有。"""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


@dataclass
class ToolResult:
    """工具执行结果。错误不抛异常，而是成为一条可读的 tool 消息。"""

    name: str
    call_id: str
    ok: bool
    output: str = ""
    error: str = ""
    denied: bool = False
    exit_code: int | None = None
    duration_ms: float = 0.0

    def to_message(self) -> dict:
        if self.ok:
            body = self.output
        elif self.denied:
            body = f"[DENIED] {self.error}"
        else:
            body = f"[ERROR] {self.error}"
        return tool_message(self.call_id, self.name, body)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "call_id": self.call_id,
            "ok": self.ok,
            "denied": self.denied,
            "exit_code": self.exit_code,
            "duration_ms": round(self.duration_ms, 2),
            "output": self.output,
            "error": self.error,
        }
