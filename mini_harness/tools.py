"""工具系统：Tool 描述、注册表、执行上下文，以及六个内置工具。

三个设计要点，对应站点里讲的「工具面」：

1. **自注册**：工具在模块加载时 `registry.add(...)`，不需要维护中央 import 列表。
2. **错误进对话**：handler 抛错由 `ToolRegistry.call` 捕获并转成 tool 消息，
   绝不让异常炸穿循环。
3. **schema 与执行分离**：`schemas()` 决定发给模型的工具面（按 toolset / 权限模式
   过滤），`call()` 决定真的怎么执行 —— 两层都要限制，不能只靠提示词。
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .errors import TimeoutToolError, ToolError
from .sandbox import LocalSandbox, SandboxLimits
from .types import ToolCall, ToolResult
from .util import truncate_middle


# --------------------------------------------------------------------------- #
# 工具定义与执行上下文
# --------------------------------------------------------------------------- #
@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict, "ToolContext"], str]
    read_only: bool = False
    group: str = "core"

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolContext:
    """一次 Agent 运行期间共享的可变状态。

    `read_paths` 是给 edit_file 用的：站点里明确要求「先 Read 再 Edit」，
    否则 old_string 匹配失败或误改。这里把该约束下沉到工具层，而不是靠模型自觉。
    """

    workspace: Path
    read_paths: set[str] = field(default_factory=set)
    memory: Any = None
    skills: Any = None
    agent: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    # -- 注册 ------------------------------------------------------------- #
    def add(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"工具重复注册: {tool.name}")
        self._tools[tool.name] = tool
        return tool

    def tool(self, *, name: str, description: str, parameters: dict, read_only: bool = False, group: str = "core"):
        """装饰器写法：@registry.tool(name="read_file", ...)"""

        def deco(fn: Callable[[dict, ToolContext], str]) -> Callable:
            self.add(Tool(name, description, parameters, fn, read_only=read_only, group=group))
            return fn

        return deco

    # -- 查询 ------------------------------------------------------------- #
    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self, groups: Iterable[str] | None = None, only: Iterable[str] | None = None) -> list[str]:
        subset = [t for t in self._tools.values() if groups is None or t.group in set(groups)]
        if only is not None:
            allowed = set(only)
            subset = [t for t in subset if t.name in allowed]
        return [t.name for t in subset]

    def schemas(self, groups: Iterable[str] | None = None, only: Iterable[str] | None = None) -> list[dict]:
        return [self._tools[n].schema() for n in self.names(groups, only)]

    def is_read_only(self, name: str) -> bool:
        tool = self._tools.get(name)
        return bool(tool and tool.read_only)

    def close(self) -> None:
        self._tools.clear()

    # -- 执行 ------------------------------------------------------------- #
    def call(self, call: ToolCall, ctx: ToolContext, timeout: float | None = None) -> ToolResult:
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult(
                name=call.name,
                call_id=call.id,
                ok=False,
                error=f"没有名为 {call.name!r} 的工具。可用工具: {', '.join(sorted(self._tools))}",
            )
        if "__parse_error__" in call.arguments:
            return ToolResult(
                name=call.name,
                call_id=call.id,
                ok=False,
                error=str(call.arguments["__parse_error__"]),
                error_code="BAD_ARGUMENTS",
                meta={"hint": "arguments 不是合法 JSON；请重新生成符合 schema 的参数。"},
            )
        t0 = time.perf_counter()
        try:
            output = tool.handler(call.arguments, ctx)
            ok, error = True, ""
        except Exception as exc:  # noqa: BLE001 —— 故意兜底，错误要变成对话内容
            from .errors import classify

            code, retryable, hint = classify(exc)
            return ToolResult(
                name=call.name,
                call_id=call.id,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=(time.perf_counter() - t0) * 1000.0,
                error_code=code,
                retryable=retryable,
                meta={"hint": hint} if hint else {},
            )
        return ToolResult(
            name=call.name,
            call_id=call.id,
            ok=ok,
            output=str(output),
            error=error,
            duration_ms=(time.perf_counter() - t0) * 1000.0,
        )


# --------------------------------------------------------------------------- #
# 路径工具：所有文件类工具都必须经过这里，沙箱才有意义
# --------------------------------------------------------------------------- #
def resolve_in_workspace(ctx: ToolContext, raw: str | None) -> Path:
    """把模型给相对路径解析到工作区内；越界直接抛错（由 call() 转成 tool 消息）。"""
    candidate = Path(raw or ".").expanduser()
    base = ctx.workspace.resolve()
    resolved = (base / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    if resolved != base and not resolved.is_relative_to(base):
        raise PermissionError(f"路径越界：{raw!r} 不在工作区 {base} 内（沙箱拒绝）")
    return resolved


def _rel(ctx: ToolContext, path: Path) -> str:
    try:
        return path.relative_to(ctx.workspace.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


# --------------------------------------------------------------------------- #
# 六个内置工具
# --------------------------------------------------------------------------- #
def build_core_registry() -> ToolRegistry:
    reg = ToolRegistry()

    @reg.tool(
        name="list_dir",
        description="列出工作区内某个目录的条目（名称、类型、大小）。只读。",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "相对工作区的目录路径，默认 ."}},
            "required": [],
        },
        read_only=True,
        group="core",
    )
    def list_dir(args: dict, ctx: ToolContext) -> str:
        target = resolve_in_workspace(ctx, args.get("path", "."))
        if not target.is_dir():
            raise NotADirectoryError(f"{_rel(ctx, target)} 不是目录")
        rows = []
        for i, entry in enumerate(sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))):
            if i >= 300:
                rows.append("…（条目过多，已截断）")
                break
            if entry.is_dir():
                rows.append(f"{entry.name}/")
            else:
                rows.append(f"{entry.name}\t{entry.stat().st_size}B")
        head = f"{_rel(ctx, target)} 共 {len(list(target.iterdir()))} 项："
        return head + "\n" + "\n".join(rows)

    @reg.tool(
        name="read_file",
        description="读取工作区内一个文本文件，可选行号范围。只读；读过的文件才允许 edit_file。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对工作区的文件路径"},
                "start_line": {"type": "integer", "description": "起始行，1 起，默认 1"},
                "max_lines": {"type": "integer", "description": "最多读取行数，默认 400"},
            },
            "required": ["path"],
        },
        read_only=True,
        group="core",
    )
    def read_file(args: dict, ctx: ToolContext) -> str:
        target = resolve_in_workspace(ctx, args.get("path"))
        if not target.is_file():
            raise FileNotFoundError(f"文件不存在：{_rel(ctx, target)}")
        start = max(1, int(args.get("start_line") or 1))
        limit = max(1, min(2000, int(args.get("max_lines") or 400)))
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        chunk = lines[start - 1 : start - 1 + limit]
        numbered = "\n".join(f"{start + i:>5}| {line}" for i, line in enumerate(chunk))
        ctx.read_paths.add(_rel(ctx, target))
        suffix = "" if start - 1 + limit >= len(lines) else f"\n…（共 {len(lines)} 行，已到读取上限）"
        return numbered + suffix

    @reg.tool(
        name="write_file",
        description="在工作区内创建或整文件覆盖写入。需要权限批准。会记录到 read_paths（写后可直接 edit）。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
        group="core",
    )
    def write_file(args: dict, ctx: ToolContext) -> str:
        target = resolve_in_workspace(ctx, args.get("path"))
        content = args.get("content")
        if not isinstance(content, str):
            raise TypeError("content 必须是字符串")
        existed = target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        ctx.read_paths.add(_rel(ctx, target))
        return f"{'覆盖' if existed else '创建'} {_rel(ctx, target)}，{len(content)} 字符，{content.count(chr(10)) + 1} 行"

    @reg.tool(
        name="edit_file",
        description="对已读过的文件做精确字符串替换（先 read_file，再 edit_file）。需要权限批准。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string", "description": "要被替换的原文，必须唯一或配合 replace_all"},
                "new_string": {"type": "string"},
                "replace_all": {"type": "boolean", "description": "默认 false；true 时替换全部匹配"},
            },
            "required": ["path", "old_string", "new_string"],
        },
        group="core",
    )
    def edit_file(args: dict, ctx: ToolContext) -> str:
        target = resolve_in_workspace(ctx, args.get("path"))
        rel = _rel(ctx, target)
        if rel not in ctx.read_paths:
            raise PermissionError(f"拒绝盲改：本会话尚未读取 {rel}。请先 read_file 再 edit_file")
        old = args.get("old_string") or ""
        new = args.get("new_string") or ""
        if not old:
            raise ValueError("old_string 不能为空")
        text = target.read_text(encoding="utf-8")
        count = text.count(old)
        if count == 0:
            raise ValueError(f"old_string 未匹配到任何内容（{rel} 可能已被外部修改，请重新 read_file）")
        if count > 1 and not args.get("replace_all"):
            raise ValueError(f"old_string 匹配到 {count} 处，请加长上下文或设置 replace_all=true")
        updated = text.replace(old, new) if args.get("replace_all") else text.replace(old, new, 1)
        target.write_text(updated, encoding="utf-8", newline="\n")
        return f"已修改 {rel}：替换 {count if args.get('replace_all') else 1} 处，{len(text)} → {len(updated)} 字符"

    @reg.tool(
        name="bash",
        description="在工作区执行一条 shell 命令（Windows 走 cmd.exe，其他平台走 /bin/sh）。需要权限批准，默认 120s 超时。",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "number", "description": "秒，默认 120，上限 600"},
            },
            "required": ["command"],
        },
        group="shell",
    )
    def bash(args: dict, ctx: ToolContext) -> str:
        command = str(args.get("command") or "").strip()
        if not command:
            raise ValueError("command 不能为空")
        timeout = max(1.0, min(600.0, float(args.get("timeout") or 120)))
        sandbox = ctx.extra.get("sandbox") or LocalSandbox()
        result = sandbox.exec(
            command,
            cwd=Path(ctx.workspace),
            limits=SandboxLimits(timeout_s=timeout),
            cancel_event=ctx.extra.get("cancel_event"),
        )
        if result.cancelled:
            raise ToolError("命令因任务取消而终止", code="CANCELLED", hint="任务已被取消，不要再重试。")
        if result.timed_out:
            raise TimeoutToolError(
                f"命令超时（>{timeout:.0f}s），进程组已被终止",
                hint="缩小命令范围；确实需要长时间运行请改用后台任务。",
            )
        parts = [f"exit_code={result.exit_code}"]
        if result.stdout.strip():
            parts.append("stdout:\n" + result.stdout.rstrip())
        if result.stderr.strip():
            parts.append("stderr:\n" + result.stderr.rstrip())
        if result.truncated:
            parts.append("（输出过长，已保留末尾部分）")
        if result.exit_code != 0:
            # 不抛异常：让模型自己读报错并决定下一步，是「失败可恢复」的关键
            parts.append("（命令以非零码退出，修复后重试）")
        return "\n".join(parts)

    @reg.tool(
        name="fetch",
        description="抓取一个 HTTP(S) URL 并返回状态码与前 8000 字符文本。只读。",
        parameters={
            "type": "object",
            "properties": {"url": {"type": "string"}, "timeout": {"type": "number", "description": "秒，默认 15"}},
            "required": ["url"],
        },
        read_only=True,
        group="web",
    )
    def fetch(args: dict, ctx: ToolContext) -> str:
        url = str(args.get("url") or "")
        if not url.startswith(("http://", "https://")):
            raise ValueError("url 必须以 http:// 或 https:// 开头")
        req = urllib.request.Request(url, headers={"User-Agent": "mini-harness/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=float(args.get("timeout") or 15)) as resp:
                body = resp.read(200_000).decode("utf-8", "replace")
                return f"HTTP {resp.status}\n" + truncate_middle(body, 8000)
        except urllib.error.HTTPError as exc:
            return f"HTTP {exc.code}（{exc.reason}）"

    return reg
