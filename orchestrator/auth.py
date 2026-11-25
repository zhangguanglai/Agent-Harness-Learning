"""JWT 鉴权（HS256，标准库实现）。

生产接 OIDC 时应换成 RS256/JWKS 校验（需要 `cryptography` 或 `PyJWT`）；
这里用 HS256 把「token → claims → Principal」的链路跑通，接口保持一致：

    authenticator.authenticate(authorization_header) -> Principal

claims 约定：
- `sub`      用户
- `tenant`   租户（必须）
- `roles`    角色数组或空格分隔字符串
- `scope`    空格分隔的 scope（可选，缺省按角色给）
- `exp`/`iss`/`aud` 按需校验
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from api.auth import AuthError, Principal


def _b64decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def decode_hs256(token: str, secret: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError("JWT 格式错误")
    header_b64, payload_b64, signature_b64 = parts
    try:
        header = json.loads(_b64decode(header_b64))
        payload = json.loads(_b64decode(payload_b64))
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthError(f"JWT 解析失败：{exc}") from exc
    if header.get("alg") != "HS256":
        raise AuthError(f"不支持的算法：{header.get('alg')}（本实现只支持 HS256）")
    expected = hmac.new(secret.encode(), f"{header_b64}.{payload_b64}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _b64decode(signature_b64)):
        raise AuthError("JWT 签名无效")
    now = time.time()
    if "exp" in payload and float(payload["exp"]) < now:
        raise AuthError("JWT 已过期")
    if "nbf" in payload and float(payload["nbf"]) > now + 60:
        raise AuthError("JWT 尚未生效")
    return payload


class JwtAuthenticator:
    def __init__(
        self,
        secret: str,
        *,
        issuer: str | None = None,
        audience: str | None = None,
        default_roles: tuple[str, ...] = ("operator",),
    ):
        self.secret = secret
        self.issuer = issuer
        self.audience = audience
        self.default_roles = default_roles

    def authenticate(self, header: str | None) -> Principal:
        if not header:
            raise AuthError("缺少 Authorization 头")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise AuthError("仅支持 Bearer token")
        claims = decode_hs256(token.strip(), self.secret)
        if self.issuer and claims.get("iss") != self.issuer:
            raise AuthError("JWT issuer 不匹配")
        if self.audience:
            audience = claims.get("aud")
            audiences = audience if isinstance(audience, list) else [audience]
            if self.audience not in audiences:
                raise AuthError("JWT audience 不匹配")
        tenant = claims.get("tenant")
        if not tenant:
            raise AuthError("JWT 缺少 tenant claim")
        roles_claim = claims.get("roles") or self.default_roles
        roles = tuple(roles_claim.split()) if isinstance(roles_claim, str) else tuple(roles_claim)
        scopes_claim = claims.get("scope")
        scopes = (
            tuple(scopes_claim.split())
            if isinstance(scopes_claim, str)
            else ("runs:write", "runs:read", "approvals:read", "approvals:write")
        )
        return Principal(
            token=f"jwt:{claims.get('jti', claims.get('sub', 'unknown'))}",
            tenant=str(tenant),
            user=str(claims.get("sub") or claims.get("user") or "unknown"),
            roles=roles,
            scopes=scopes,
        )
