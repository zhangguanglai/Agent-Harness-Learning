"""子代理：独立上下文里干活，只回传结论。

站点里的三条经验，这里用最小实现体现：

1. **上下文隔离**：子代理的中间探索不进入父上下文，父会话只拿到几千字以内的结论；
2. **权限不外溢**：子代理继承父会话的权限模式与审批人，不能自己「升级」；
3. **深度上限**：深度 1 即止，避免代理自我复制导致指数级消耗。

工程上它是一个普通工具 —— 这也解释了为什么「多代理」不需要新协议。
"""

from __future__ import annotations

from typing import Callable, Sequence

from .tools import ToolContext, ToolRegistry

Runner = Callable[[str, "Sequence[str] | None"], str]


def register_delegate(registry: ToolRegistry, runner: Runner) -> None:
    @registry.tool(
        name="delegate",
        description=(
            "把一段自包含的子任务交给子代理执行。子代理有独立上下文，只回传结论摘要；"
            "适合「并行调研一个大目录 / 独立验证一个假设」这类会弄脏主上下文的工作。"
            "子任务描述必须自包含：写清目标、范围、验收标准。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "自包含的子任务描述"},
                "tools": {"type": "array", "items": {"type": "string"}, "description": "可选的工具白名单，默认继承父配置"},
            },
            "required": ["goal"],
        },
        group="delegate",
    )
    def delegate(args: dict, ctx: ToolContext) -> str:
        goal = str(args.get("goal") or "").strip()
        if not goal:
            raise ValueError("goal 不能为空")
        tools = args.get("tools")
        return runner(goal, tools if isinstance(tools, list) else None)
