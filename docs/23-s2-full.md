# 23 · S2 正式项：JWT、配额、双人审批

> 在 [S2 前奏](21-s2-auth-tenancy.md) 之上补三件事：真正的 token 验证、租户配额、双人审批与升级。
> 代码：[orchestrator/auth.py](../orchestrator/auth.py)、[quota.py](../orchestrator/quota.py)、
> [service.py](../orchestrator/service.py)、实验 [lab17](../labs/lab17_s2_full.py)。

## 1. JWT 鉴权（HS256）

```bash
python -m orchestrator --port 8081 \
  --jwt-secret "$JWT_SECRET" --jwt-issuer harness --jwt-audience banking-a
```

```bash
curl -XPOST localhost:8081/v1/assessments \
  -H "Authorization: Bearer $JWT" -H 'content-type: application/json' \
  -d '{"case_id":"CASE-04"}'
```

claims 约定：

| claim | 用途 |
| --- | --- |
| `tenant` | 租户（必需）；请求体不能覆盖 |
| `sub` | 用户 |
| `roles` | 角色数组（`operator` / `supervisor` / `viewer`） |
| `scope` | 可选；缺省给全量 scope |
| `exp` / `nbf` / `iss` / `aud` | 过期、生效、签发方、受众校验 |

实现是 **HS256 + 标准库**（`hmac`/`hashlib`），够跑通链路。生产接 OIDC 时换成
RS256/JWKS（`PyJWT` + `cryptography`），`JwtAuthenticator.authenticate` 接口不变。

> 为什么不用静态 token 表？静态表无法表达过期与签发方，也不利于接入企业 IdP。
> 静态 token 仍保留为开发模式。

## 2. 租户配额

```bash
python -m orchestrator \
  --quota-concurrent 3 --quota-daily-runs 200 --quota-daily-tokens 2000000
```

| 配额 | 判定 | 超限响应 |
| --- | --- | --- |
| 并发运行 | 该租户 `running/waiting_approval/queued` 数 | 429 + `retry_after_s=30` |
| 日运行数 | 当日创建的 run 数 | 429 + `retry_after_s=3600` |
| 日 token | `usage_ledger` 当日累计 | 429 + `retry_after_s=3600` |

`GET /v1/usage` 返回当前用量与上限：

```json
{ "usage": { "tenant": "acme", "active": 1, "runs_today": 12, "tokens_today": 34000 },
  "limits": { "max_concurrent": 3, "daily_runs": 200, "daily_tokens": 2000000 } }
```

配额只读数据库，不依赖进程内计数——多副本部署时才能一致。

## 3. 双人审批与升级

```bash
python -m orchestrator --dual-approval --approval-sla 14400   # 4 小时
```

- `security_assessment` 路径要求 **2 票**；其他路径 1 票；
- 第一票返回 `202 {status:"pending", approvals:1, required:2}`；
- 第二票返回 `200 {status:"approved"}` 并放行；
- 超过 SLA 未凑齐 → `approval.escalated` 事件，**supervisor 角色可单独批准**；
- 超过 2×SLA → 自动拒绝（`approval expired`），run 进入 `rejected`。

审批事件：`approval.requested`（含 required）→ `approval.escalated` → `approval.resolved`（含 by）。

## 4. 实测（lab17）

```
✓ JWT：claims → Principal，签名与 tenant 校验生效
✓ JWT 接入：正确 token 可提交，错误签名 401
✓ 配额：日运行上限 1，第二次 429；usage.runs_today=1
✓ 双人审批：1/2 仍 pending，第二票后放行，结论 completed
✓ 升级：SLA 超时 → approval.escalated → supervisor 单人批准
```

## 5. 还没做的（S2 尾声）

| 项 | 现状 | 下一步 |
| --- | --- | --- |
| 对象存储产物 | `ArtifactStore` 协议 + 本地/内存 | 实现 `S3ArtifactStore`（SigV4 PUT + 预签名下载） |
| OIDC 完整流程 | HS256 共享密钥 | 接 IdP 的 JWKS、RS256、token 刷新 |
| 计费出账 | usage_ledger 记录 | 按租户出账、预算告警、对账 |
| 数据隔离 | 逻辑隔离（tenant 过滤） | schema/库级隔离、密钥隔离 |
| worker 鉴权 | 服务间无认证 | mTLS + 短期 token |

## 6. 自测

1. 为什么 `tenant` 必须来自 token，而不能来自请求体？
2. 配额为什么读数据库而不是内存计数？多副本时有什么差别？
3. 双人审批里，第一票返回 202 而不是 200，语义上有什么区别？
4. SLA 升级后为什么允许 supervisor 单人批准？这会不会削弱双人控制？
5. 如果要在现有实现上接 OIDC，改哪一层？为什么领域代码不用动？
