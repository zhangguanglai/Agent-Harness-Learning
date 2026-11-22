"""Hooks：在工具执行前后插入确定性逻辑。

与权限规则的分工：
- 权限规则回答「允不允许」，结果是 allow/deny/ask；
- Hook 回答「执行前后还要做什么」，可以审计、脱敏、格式化、二次拦截。

求值顺序 `Hook → 规则 → 沙箱` 里的 Hook 之所以靠前，是因为组织级策略
（例如「任何写 .env 的操作都记录并拒绝」）不该被个人 allow 规则绕开。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .tools import ToolContext
from .types import ToolCall, ToolResult


BeforeHook = Callable[[ToolCall, ToolContext], "str | None"]
AfterHook = Callable[[ToolCall, ToolResult, ToolContext], None]


@dataclass
class Hooks:
    before: list[BeforeHook] = field(default_factory=list)
    after: list[AfterHook] = field(default_factory=list)

    def on_before(self, fn: BeforeHook) -> BeforeHook:
        self.before.append(fn)
        return fn

    def on_after(self, fn: AfterHook) -> AfterHook:
        self.after.append(fn)
        return fn

    def run_before(self, call: ToolCall, ctx: ToolContext) -> str | None:
        """返回非空字符串 = 否决，字符串即拒绝原因。"""
        for fn in self.before:
            veto = fn(call, ctx)
            if veto:
                return veto
        return None

    def run_after(self, call: ToolCall, result: ToolResult, ctx: ToolContext) -> None:
        for fn in self.after:
            try:
                fn(call, result, ctx)
            except Exception:  # noqa: BLE001 —— 审计失败不应影响主流程
                continue


def secret_guard(call: ToolCall, ctx: ToolContext) -> str | None:
    """示例 Hook：禁止把疑似密钥写进文件。"""
    if call.name in {"write_file", "edit_file"}:
        blob = " ".join(str(v) for v in call.arguments.values())
        for marker in ("sk-", "AKIA", "-----BEGIN PRIVATE KEY-----"):
            if marker in blob:
                return f"Hook 拦截：内容疑似包含密钥（命中 {marker}），请改用环境变量"
    return None


def make_audit_hook(record: Callable[[dict], None]) -> AfterHook:
    """示例 Hook：把每次工具结果落到审计流。"""

    def hook(call: ToolCall, result: ToolResult, ctx: ToolContext) -> None:
        record(
            {
                "tool": call.name,
                "ok": result.ok,
                "denied": result.denied,
                "chars": len(result.output),
                "error": result.error[:200],
            }
        )

    return hook
