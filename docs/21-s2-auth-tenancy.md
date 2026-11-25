# 21 · S2 前奏：鉴权、租户、规则版本、产物抽象

> S1 能演示，S2 能试用。这一层把「谁在用、按哪版规则、产物存哪」变成服务端决定的事。
> 代码：[orchestrator/service.py](../orchestrator/service.py)、[artifacts.py](../orchestrator/artifacts.py)、
> 实验 [lab16](../labs/lab16_s2_auth.py)。

## 1. 鉴权：token → Principal → 租户

```bash
python -m orchestrator --port 8081 \
  --token "t-alice=acme:alice:operator" \
  --token "t-bob=beta:bob:viewer"
```

- `Authorization: Bearer <token>` → `Principal(tenant, user, roles, scopes)`；
- **租户只来自 token**，请求体里的 `tenant` 只能与 token 一致，否则 403；
- 跨租户读取/审批返回 **404**（不泄露存在性）；
- `/healthz` 免鉴权；`/v1/worker-approvals` 是服务间接口（生产应加 mTLS/worker token）。

未配置 `--token` 时进入**开发模式**（固定 tenant=demo），方便本地演示，但日志会明确标注。

## 2. 规则版本：同一个案例，两个结论

```bash
curl -XPOST localhost:8081/v1/assessments \
  -H "Authorization: Bearer t-alice" -H 'content-type: application/json' \
  -d '{"case_id":"CASE-05","rules_version":"v2"}'
```

`CASE-05`（3 万条、其中 6 千敏感）：

| 规则版本 | 阈值（敏感） | 判定 |
| --- | --- | --- |
| v1 | 10,000 | 免于申报（exempt_small） |
| v2 | 5,000 | 申报安全评估（security_assessment） |

版本随请求进入 run（`runs.rules_version`），并写进 `run.queued` 事件与最终 decision。
**规则升级不再是「改代码」，而是「换配置 + 重跑评测」**。

## 3. 产物抽象：从本地目录到对象存储

```python
class ArtifactStore(Protocol):
    def put(self, run_id: str, name: str, content: str) -> StoredObject: ...
```

| 实现 | 定位符 | 用途 |
| --- | --- | --- |
| `LocalArtifactStore` | 文件路径 | 单机 / 演示 |
| `MemoryArtifactStore` | `mem://run/name` | 测试 |
| （待补）`S3ArtifactStore` | `s3://bucket/key` | 生产 |

编排层只记录 `{path, sha256, status, version}`，存储实现替换不影响审批与审计。

## 4. 接口变化

| 接口 | 变化 |
| --- | --- |
| `POST /v1/assessments` | 新增 `rules_version`；租户取自 token |
| `GET /v1/assessments/{id}` | 返回 `rules_version`；跨租户 404 |
| `GET /v1/approvals` | 按 token 租户过滤 |
| `POST /v1/assessments/{id}/approve` | 校验租户；批准人默认取 token 用户 |
| `GET /v1/assessments/{id}/artifacts` | 路径来自 ArtifactStore（本地/内存/对象存储） |

## 5. 实测（lab16）

```
✓ 鉴权：/healthz 免鉴权，缺失/错误 token 均 401
✓ 规则版本：CASE-05 在 v1 免申报、v2 判安全评估
✓ 隔离：跨租户读取/审批 404，审批列表按租户过滤
✓ 产物：final v3，sha256 可校验，路径可读
✓ 抽象：MemoryArtifactStore 与 LocalArtifactStore 同接口
```

> 正式项（JWT / 配额 / 双人审批）见 [23 S2 正式项](23-s2-full.md)。

## 6. S2 还差什么

| 项 | 现状 | 下一步 |
| --- | --- | --- |
| 身份 | 静态 token 表 | 接 OIDC/JWT（issuer/audience/scope） |
| 租户 | 逻辑隔离（过滤 + 404） | 数据隔离（schema/库）、密钥隔离 |
| 配额 | 无 | 并发/日 token/日运行三类配额 |
| 计费 | 只有 usage | 账本 + 对账 + 预算告警 |
| 审批流 | 单级 | 双人、超时升级、IM 通知 |
| 产物 | 本地/内存 | S3/OSS + 预签名下载 |
| worker 鉴权 | 无 | mTLS + 短期 token |

## 7. 自测

1. 为什么租户必须来自 token，而不是请求体？
2. 跨租户为什么返回 404 而不是 403？
3. `rules_version` 放在请求里而不是服务端全局配置，好处与风险各是什么？
4. 产物抽象里为什么 `path` 要允许 `s3://` 这类非文件定位符？
5. 开发模式（无 token）在生产误开会导致什么？怎么防？
