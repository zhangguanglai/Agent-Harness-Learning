"""工具网关：所有工具调用的唯一入口。

它承担四件事，正好是 mini_harness 里 `ToolRegistry.call` 直接调用所缺的：

1. **schema 版本**：工具描述变化要能被追踪（提示缓存、回归都要用）；
2. **按主体裁剪工具面**：viewer 看不到 `bash`，schema 里也不会出现；
3. **受控重试**：只重试「可重试 + 只读」的错误，有副作用的工具绝不自动重试；
4. **错误码统计**：给指标与告警用（`stats`）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

from mini_harness.tools import ToolContext, ToolRegistry
from mini_harness.types import ToolCall, ToolResult


@dataclass(frozen=True)
class ToolSpec:
    name: str
    version: str
    group: str
    read_only: bool
    description: str


class ToolGateway:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        schema_version: str = "1",
        max_attempts: int = 2,
        backoff_s: float = 0.05,
        retry_codes: Iterable[str] = ("TIMEOUT", "RATE_LIMITED", "NETWORK"),
    ):
        self.registry = registry
        self.schema_version = schema_version
        self.max_attempts = max(1, max_attempts)
        self.backoff_s = backoff_s
        self.retry_codes = frozenset(retry_codes)
        self.stats: dict[str, int] = {}

    # -- 工具面 ------------------------------------------------------------ #
    def specs(self) -> list[ToolSpec]:
        specs = []
        for name in self.registry.names():
            tool = self.registry.get(name)
            if tool is None:
                continue
            specs.append(
                ToolSpec(
                    name=tool.name,
                    version=self.schema_version,
                    group=tool.group,
                    read_only=tool.read_only,
                    description=tool.description,
                )
            )
        return specs

    def schemas(self, allowed: set[str] | None = None) -> list[dict]:
        names = [n for n in self.registry.names() if allowed is None or n in allowed]
        return [self.registry.get(n).schema() for n in names if self.registry.get(n)]

    # -- 执行 -------------------------------------------------------------- #
    def invoke(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        tool = self.registry.get(call.name)
        attempts = 1
        result = self.registry.call(call, ctx)
        while (
            not result.ok
            and result.retryable
            and result.error_code in self.retry_codes
            and tool is not None
            and tool.read_only
            and attempts < self.max_attempts
        ):
            attempts += 1
            time.sleep(self.backoff_s * attempts)
            result = self.registry.call(call, ctx)

        if not result.ok and result.retryable and tool is not None and not tool.read_only:
            # 有副作用的工具不自动重试：宁可让模型/人决定，也不冒重复写入的风险
            result.meta["retry_suppressed"] = "side_effect_tool"
        result.meta["attempts"] = attempts

        code = result.error_code or ("OK" if result.ok else "INTERNAL")
        self.stats[code] = self.stats.get(code, 0) + 1
        return result
