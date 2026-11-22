"""通用小工具：token 估算、文本截断、JSONL 追加、ID 生成。

设计立场：这个仓库不引第三方依赖，所有能力用标准库实现，
这样「读代码 → 跑实验」零安装，学习者可以把注意力放在机制本身。
"""

from __future__ import annotations

import json
import math
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def new_id(prefix: str = "id") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def estimate_tokens(text: str) -> int:
    """粗估 token 数：CJK 按 1 字 1 token，其余按 4 字符 1 token。

    这不是分词器，只用于「上下文预算」和「压缩触发」这类需要稳定、
    可离线复现的判断。真实计费请以 provider 返回的 usage 为准。
    """
    if not text:
        return 0
    cjk = 0
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u30ff":
            cjk += 1
    other = max(0, len(text) - cjk)
    return cjk + math.ceil(other / 4)


def estimate_messages_tokens(messages: list[dict]) -> int:
    """估算消息列表的 token，含每条消息约 4 token 的结构开销。"""
    total = 0
    for msg in messages:
        total += 4
        total += estimate_tokens(str(msg.get("content") or ""))
        for call in msg.get("tool_calls") or []:
            fn = call.get("function", {})
            total += estimate_tokens(fn.get("name", ""))
            total += estimate_tokens(str(fn.get("arguments", "")))
    return total


def truncate_middle(text: str, limit: int, marker: str = "…（已截断 {n} 字符）…") -> str:
    """头尾保留式截断：模型的判断往往依赖开头（结论）和结尾（报错）。"""
    if limit <= 0 or len(text) <= limit:
        return text
    keep = max(1, limit - 40)
    head = keep * 3 // 5
    tail = keep - head
    omitted = len(text) - head - tail
    return text[:head] + marker.format(n=omitted) + text[-tail:]


def ensure_dir(path: "str | os.PathLike[str]") -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def jsonl_append(path: "str | os.PathLike[str]", record: dict) -> None:
    ensure_dir(Path(path).parent)
    line = json.dumps(record, ensure_ascii=False, default=str)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def read_text(path: "str | os.PathLike[str]", default: str = "") -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except FileNotFoundError:
        return default


def write_text(path: "str | os.PathLike[str]", text: str) -> Path:
    p = Path(path)
    ensure_dir(p.parent)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return p
