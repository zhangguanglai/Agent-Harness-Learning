"""权限层：模式 + 规则 + 沙箱。

三层结构（与 Claude Code 的心智模型一致）：

1. **模式**决定整体策略（default / acceptEdits / plan / dontAsk / bypassPermissions）；
2. **规则**用 `Tool(spec)` 语法做细粒度 allow / deny / ask，求值顺序 deny → ask → allow；
3. **沙箱**在规则之前硬拦越界路径，并优先拦下灾难性命令 —— 即使 bypass 也不放过。

关键认知：提示词管「模型想做什么」，权限管「允许做什么」。在 AGENTS.md 里
写「禁止删库」不构成任何约束，只有这里的规则才是。
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Sequence

from .tools import ToolContext, resolve_in_workspace
from .types import ToolCall


class Mode(str, Enum):
    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    PLAN = "plan"
    DONT_ASK = "dontAsk"
    BYPASS = "bypassPermissions"


#: 与工具无关的硬拦截：这些命令一律 deny，任何模式都不放行。
DANGEROUS_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+\s*/", "递归删除根目录"),
    (r"\bmkfs(\.\w+)?\b", "格式化文件系统"),
    (r"\bdd\s+.*of=/dev/", "直接写块设备"),
    (r"\bformat\s+[a-zA-Z]:", "格式化磁盘"),
    (r"\bdel\s+/[fsq].*[a-zA-Z]:\\(\s|$|\*)", "递归删除盘符根目录"),
    (r"\bshutdown\b|\breboot\b", "关机或重启"),
    (r":\(\)\s*\{\s*:\|:&\s*\};:", "fork 炸弹"),
)

PATH_TOOLS = {"read_file", "write_file", "edit_file", "list_dir"}
WRITE_TOOLS = {"write_file", "edit_file"}


@dataclass
class Decision:
    action: str  # allow | deny | ask
    reason: str
    rule: str = ""

    @property
    def allowed(self) -> bool:
        return self.action == "allow"

    def __str__(self) -> str:
        tag = {"allow": "ALLOW", "deny": "DENY", "ask": "ASK"}[self.action]
        return f"{tag} · {self.reason}" + (f"（规则 {self.rule}）" if self.rule else "")


def _norm_path(raw: str | None) -> str:
    path = (raw or ".").replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path.rstrip("/") or "."


def _match_spec(tool: str, spec: str, call: ToolCall, ctx: ToolContext) -> bool:
    """判断规则里的 specifier 是否命中本次调用。

    - bash：对整条命令做 glob；`npm run test *` 这类前缀规则也成立；
      复合命令按 `&&`、`;`、`|` 切开逐段匹配（任一段命中即算命中）。
    - 文件工具：对归一化后的相对路径做 glob，`./.env`、`**/*.pem` 都可写。
    - 其他工具：对 JSON 形式的参数做 glob。
    """
    args = call.arguments or {}
    if tool == "bash":
        command = str(args.get("command", ""))
        segments = [s.strip() for s in re.split(r"&&|\|\||;|\|", command) if s.strip()] or [command]
        candidates = [command, *segments]
        return any(fnmatch.fnmatchcase(c, spec) or fnmatch.fnmatchcase(c, spec.rstrip("*").strip() + "*") for c in candidates)
    if tool in PATH_TOOLS:
        return fnmatch.fnmatchcase(_norm_path(args.get("path")), _norm_path(spec))
    flat = json.dumps(args, ensure_ascii=False, sort_keys=True)
    return fnmatch.fnmatchcase(flat, spec) or fnmatch.fnmatchcase(flat, f"*{spec}*")


@dataclass
class Rule:
    raw: str
    action: str  # allow | deny | ask
    tool: str
    spec: str | None
    valid: bool = True
    error: str = ""

    @classmethod
    def parse(cls, raw: str, action: str) -> "Rule":
        text = raw.strip()
        if not text:
            return cls(raw, action, "", None, valid=False, error="空规则")
        if "(" not in text:
            return cls(raw, action, text, None)
        name, _, rest = text.partition("(")
        spec = rest[:-1] if rest.endswith(")") else rest
        return cls(raw, action, name.strip(), spec.strip())

    def describe(self) -> str:
        return f"{self.action}: {self.raw}"


class PermissionPolicy:
    def __init__(
        self,
        mode: Mode = Mode.DEFAULT,
        allow: Sequence[str] = (),
        deny: Sequence[str] = (),
        ask: Sequence[str] = (),
        sandbox_root: Path | None = None,
    ):
        self.mode = Mode(mode)
        self.allow = [Rule.parse(r, "allow") for r in allow]
        self.deny = [Rule.parse(r, "deny") for r in deny]
        self.ask = [Rule.parse(r, "ask") for r in ask]
        self.sandbox_root = sandbox_root

    # -- 对外唯一入口 ------------------------------------------------------ #
    def decide(self, call: ToolCall, ctx: ToolContext) -> Decision:
        tool = call.name

        # 1) 灾难性命令：先于一切规则，bypass 也不放行
        if tool == "bash":
            command = str(call.arguments.get("command", ""))
            for pattern, why in DANGEROUS_PATTERNS:
                if re.search(pattern, command, flags=re.IGNORECASE):
                    return Decision("deny", f"命中灾难性命令黑名单（{why}），任何模式都不执行")

        # 2) 派生工具本身不弹窗：与 Claude Code 一致，风险在子代理内部的工具调用，
        #    而子代理继承同一套模式与审批人，仍然受管。
        if tool == "delegate":
            return Decision("allow", "子代理派生本身不需要确认；子代理内部的工具调用仍走同一套权限")

        # 3) 沙箱：路径类工具必须在工作区内
        if tool in PATH_TOOLS:
            try:
                resolve_in_workspace(ctx, call.arguments.get("path"))
            except PermissionError as exc:
                return Decision("deny", str(exc))

        # 4) plan 模式：只读工具之外一律拒绝
        if self.mode is Mode.PLAN and not ctx_tool_read_only(ctx, tool):
            return Decision("deny", "plan 模式只做只读探索；如需写入，请切到 default 或 acceptEdits 模式")

        # 5) 规则：deny → ask → allow
        for rule in self.deny:
            if rule.valid and self._hit(rule, call, ctx):
                return Decision("deny", f"被规则 {rule.raw} 拒绝", rule.raw)
        for rule in self.ask:
            if rule.valid and self._hit(rule, call, ctx):
                return Decision("ask", f"规则 {rule.raw} 要求逐次确认", rule.raw)
        for rule in self.allow:
            if rule.valid and self._hit(rule, call, ctx):
                return Decision("allow", f"被规则 {rule.raw} 预授权", rule.raw)

        # 6) 模式兜底
        if self.mode is Mode.BYPASS:
            return Decision("allow", "bypassPermissions 模式：跳过确认（沙箱与黑名单仍生效）")
        if self.mode is Mode.DONT_ASK:
            return Decision("deny", "dontAsk 模式：未预授权的操作一律拒绝（适合 CI）")
        if ctx_tool_read_only(ctx, tool):
            return Decision("allow", "只读工具默认放行")
        if self.mode is Mode.ACCEPT_EDITS and tool in WRITE_TOOLS:
            return Decision("allow", "acceptEdits 模式：工作区内编辑自动接受")
        return Decision("ask", "写操作或命令执行需要人工确认")

    # -- 内部 -------------------------------------------------------------- #
    def _hit(self, rule: Rule, call: ToolCall, ctx: ToolContext) -> bool:
        # 规则里习惯写工具名首字母大写（Bash/Read），实现层是小写，这里做大小写无关匹配
        if rule.tool.lower() != call.name.lower():
            return False
        if rule.spec is None:
            return True
        return _match_spec(call.name, rule.spec, call, ctx)

    def describe(self) -> str:
        rules = self.allow + self.ask + self.deny
        lines = [f"权限模式: {self.mode.value}"]
        lines += [f"- {r.describe()}" for r in rules if r.valid]
        for r in rules:
            if not r.valid:
                lines.append(f"- （忽略无效规则）{r.raw}: {r.error}")
        return "\n".join(lines)


def ctx_tool_read_only(ctx: ToolContext, tool: str) -> bool:
    """借助注册表查询工具是否只读；拿不到时保守视为可写。"""
    registry = (ctx.extra or {}).get("registry")
    return bool(registry and registry.is_read_only(tool))
