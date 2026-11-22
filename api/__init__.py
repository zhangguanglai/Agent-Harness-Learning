"""HTTP API 服务（Phase 1 第 ② 步）。

    python -m api --port 8765 --token "dev-token=demo:alice:operator"

    curl -s localhost:8765/health
    curl -s -H "Authorization: Bearer dev-token" -H "Content-Type: application/json" \
         -d '{"task":"看看工作区","session_id":"s1"}' localhost:8765/v1/runs

设计说明见 `docs/13-http-api.md`。
"""

from .auth import AuthError, ForbiddenError, Principal, RateLimiter, TokenRegistry
from .client import ApiError, HarnessClient
from .server import API_VERSION, ApiConfig, HarnessAPI

__all__ = [
    "HarnessAPI",
    "ApiConfig",
    "API_VERSION",
    "HarnessClient",
    "ApiError",
    "TokenRegistry",
    "Principal",
    "RateLimiter",
    "AuthError",
    "ForbiddenError",
]
