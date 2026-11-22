"""鉴权与限流：API 层的两个最小闸门。

- 鉴权：`Authorization: Bearer <token>` → `Principal`（租户/用户/角色）。
  **角色只能来自 token，不能来自请求体**，否则客户端就能自我提权。
- 限流：按 token 的令牌桶；超限返回 429，避免一个客户端打满整个运行时。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Principal:
    token: str
    tenant: str
    user: str
    roles: tuple[str, ...] = ("operator",)
    scopes: tuple[str, ...] = ("runs:write", "runs:read", "approvals:read", "approvals:write")

    def has(self, scope: str) -> bool:
        return scope in self.scopes


class AuthError(Exception):
    """401：缺少或无效凭证。"""


class ForbiddenError(Exception):
    """403：凭证有效但没有该 scope。"""


class TokenRegistry:
    """token → Principal 的静态表（生产换 OIDC/JWT 校验）。"""

    def __init__(self, principals: dict[str, Principal]):
        self._principals = dict(principals)

    @classmethod
    def from_specs(cls, specs: list[str]) -> "TokenRegistry":
        """`token=tenant:user:role1,role2` 形式的命令行配置。"""
        registry: dict[str, Principal] = {}
        for spec in specs:
            token, _, rest = spec.partition("=")
            if not token or not rest:
                raise ValueError(f"无效的 token 配置: {spec!r}（应为 token=tenant:user:roles）")
            parts = rest.split(":")
            if len(parts) < 2:
                raise ValueError(f"无效的 token 配置: {spec!r}")
            tenant, user = parts[0], parts[1]
            roles = tuple((parts[2] if len(parts) > 2 else "operator").split(","))
            registry[token] = Principal(token=token, tenant=tenant, user=user, roles=roles)
        return cls(registry)

    def authenticate(self, header: str | None) -> Principal:
        if not header:
            raise AuthError("缺少 Authorization 头")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise AuthError("仅支持 Bearer token")
        principal = self._principals.get(token.strip())
        if principal is None:
            raise AuthError("无效 token")
        return principal


@dataclass
class RateLimiter:
    """令牌桶：capacity 个突发，按 rate_per_s 匀速补充。"""

    capacity: float = 20.0
    rate_per_s: float = 20.0
    _buckets: dict[str, tuple[float, float]] = field(default_factory=dict)  # key -> (tokens, last_ts)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def allow(self, key: str, cost: float = 1.0) -> bool:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate_per_s)
            if tokens < cost:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - cost, now)
            return True
