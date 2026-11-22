"""工具错误的分类：让「重试」不再靠猜。

生产 Harness 与教学实现的一个分界线是：错误不能只是一段字符串。
调用方需要知道这条错误**能不能重试**、**是不是权限拒绝**、**该不该告警**。

约定：
- `code` 是稳定的机器可读枚举，用于指标与重试策略；
- `retryable` 决定运行时是否自动重试（默认不重试，避免重复副作用）；
- `hint` 给模型看的下一步建议（会拼进 tool 消息）。
"""

from __future__ import annotations


class ToolError(Exception):
    """工具层的受控错误基类。未继承它的异常一律视为内部错误，不自动重试。"""

    code = "TOOL_ERROR"
    retryable = False

    def __init__(self, message: str, *, code: str | None = None, retryable: bool | None = None, hint: str = ""):
        super().__init__(message)
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable
        self.hint = hint


class RetryableToolError(ToolError):
    code = "RETRYABLE"
    retryable = True


class TimeoutToolError(RetryableToolError):
    code = "TIMEOUT"


class RateLimitToolError(RetryableToolError):
    code = "RATE_LIMITED"


class FatalToolError(ToolError):
    code = "FATAL"


class NotFoundToolError(ToolError):
    code = "NOT_FOUND"


class DeniedToolError(ToolError):
    code = "DENIED"


#: 非受控异常 → (code, retryable) 的兜底映射。
BUILTIN_ERROR_CODES: tuple[tuple[type[BaseException], str, bool], ...] = (
    (PermissionError, "DENIED", False),
    (FileNotFoundError, "NOT_FOUND", False),
    (TimeoutError, "TIMEOUT", True),
    (ConnectionError, "NETWORK", True),
)


def classify(exc: BaseException) -> tuple[str, bool, str]:
    """把任意异常映射成 (code, retryable, hint)。"""
    if isinstance(exc, ToolError):
        return exc.code, exc.retryable, exc.hint
    for exc_type, code, retryable in BUILTIN_ERROR_CODES:
        if isinstance(exc, exc_type):
            hint = ""
            if code == "DENIED":
                hint = "该操作被权限或沙箱拒绝；不要重试同样参数，改为在允许范围内操作或向用户说明。"
            elif code == "NOT_FOUND":
                hint = "目标不存在；先用 list_dir / read_file 确认真实路径。"
            elif code == "TIMEOUT":
                hint = "可以重试一次；若仍超时，缩小命令范围或改为后台执行。"
            return code, retryable, hint
    return "INTERNAL", False, "内部错误，不要原样重试；换一种做法或向用户报告。"
