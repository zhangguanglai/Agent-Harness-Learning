"""多主体权限：在「工具 + 参数」之外，加上租户 / 用户 / 角色。

mini_harness 的 `PermissionPolicy` 只回答「这条调用允不允许」；
生产还要回答「**谁**的这条调用允不允许」。本模块是它的薄包装：

1. 角色能力表先过滤（viewer 不能写、operator 能写、admin 全量）；
2. 再走原有规则（deny→ask→allow）与沙箱；
3. 决策里带上主体信息，供审计与审批流使用。
"""

from __future__ import annotations

from dataclasses import dataclass

from mini_harness.policy import Decision, PermissionPolicy
from mini_harness.tools import ToolContext
from mini_harness.types import ToolCall

READ_TOOLS = frozenset({"list_dir", "read_file", "fetch", "skill_view"})

ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    "viewer": READ_TOOLS,
    "operator": READ_TOOLS | frozenset({"write_file", "edit_file", "bash", "memory", "skill_manage", "delegate"}),
}
#: admin 不做限制（仍受沙箱与灾难命令黑名单约束）
UNRESTRICTED_ROLES = frozenset({"admin"})


@dataclass(frozen=True)
class PolicyContext:
    tenant: str
    user: str
    roles: tuple[str, ...]
    session_id: str
    run_id: str

    def describe(self) -> str:
        return f"{self.tenant}/{self.user} roles={','.join(self.roles) or 'none'}"


def capabilities_for(roles: tuple[str, ...]) -> frozenset[str] | None:
    """返回允许的工具集合；None 表示不按角色限制。"""
    if any(role in UNRESTRICTED_ROLES for role in roles):
        return None
    allowed: set[str] = set()
    for role in roles:
        allowed |= set(ROLE_CAPABILITIES.get(role, frozenset()))
    return frozenset(allowed)


class TenantPermissionPolicy(PermissionPolicy):
    """在基础策略之上叠加角色能力过滤。"""

    def __init__(self, *, ctx: PolicyContext, **kwargs):
        super().__init__(**kwargs)
        self.ctx = ctx
        self.capabilities = capabilities_for(ctx.roles)

    def decide(self, call: ToolCall, tool_ctx: ToolContext) -> Decision:
        if self.capabilities is not None and call.name not in self.capabilities:
            return Decision(
                "deny",
                f"角色 {('/'.join(self.ctx.roles) or 'anonymous')} 无权调用 {call.name}（租户 {self.ctx.tenant}）",
                rule="role",
            )
        decision = super().decide(call, tool_ctx)
        if decision.action == "deny":
            decision.reason = f"[{self.ctx.tenant}] {decision.reason}"
        return decision

    def describe(self) -> str:
        caps = "不限制" if self.capabilities is None else ", ".join(sorted(self.capabilities))
        return f"主体: {self.ctx.describe()}\n可调用工具: {caps}\n" + super().describe()
