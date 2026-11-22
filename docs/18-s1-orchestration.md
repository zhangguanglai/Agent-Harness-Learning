# 18 · S1 编排：把一次出境评估跑成可审批的 run

> 对应 [16 实施方案](16-implementation-plan.md) 的 S1；领域工具见
> [domain/banking_a](../domain/banking_a/README.md)，控制面契约见 [15](15-option-c-technical-design.md)。
> 代码：[orchestrator/](../orchestrator/)、实验 [lab13](../labs/lab13_orchestrator.py)。

## 1. 这一层解决什么

工具会算，不等于业务能交付。S1 编排补的是「一次任务的生命周期」：

```
提交 → 抽取 → 判定 → 引用校验 → 起草 → 等审批 → 定稿 → 产物可下载
```

它负责：状态机、事件流、审批闸门、产物版本；**不负责**领域判断（那是工具的事），
也不负责模型推理（那是 worker/DSH 的事）。

## 2. 事件序列

| 事件 | 何时 | 关键字段 |
| --- | --- | --- |
| `run.queued` / `run.started` | 提交/开工 | case_id, tenant |
| `extract.completed` | 材料解析完 | facts, warnings, injection_detected |
| `rules.evaluated` | 路径判定完 | path, reasons, required_evidence, citations |
| `citations.verified` | 引用自校验 | valid, missing, citations（含原文） |
| `report.drafted` | 报告草稿生成 | chars, path |
| `artifact.drafted` | 草稿产物落盘 | artifact_id, version |
| `approval.requested` | 需要审批 | approval_id, action |
| `approval.resolved` | 审批完成 | approved, by, comment |
| `artifact.finalized` | 定稿 | artifact_id, version, by |
| `assessment.completed` / `rejected` / `failed` | 终态 | path / reason |

事件只追加，状态是投影——和 [docs/12](12-runtime-skeleton.md) 的运行时同一条原则。

## 3. 状态机

```
running ──(无需审批)──────────────▶ completed
   │
   ├──(需审批)──▶ waiting_approval ──(批准)──▶ completed
   │                    └────────────(拒绝)──▶ rejected
   └──(异常/引用不合法)──────────────────────▶ failed
```

**哪些路径要审批**：`security_assessment` / `standard_contract` / `exempt_scenario`；
`exempt_small` / `not_applicable` 自动完成（仍写产物与事件）。

这条规则写在 `workflow.APPROVAL_PATHS`，改业务策略不用动流程代码。

## 4. HTTP 接口

```http
POST /v1/assessments                 { "case_id": "CASE-04", "tenant": "demo" }
                                     → 202 { run_id, events_url }
GET  /v1/assessments/{id}            状态 + decision
GET  /v1/assessments/{id}/events     JSON（?after）或 SSE（Accept: text/event-stream）
POST /v1/assessments/{id}/approve    { "approved": true, "by": "compliance.officer", "comment": "..." }
GET  /v1/assessments/{id}/artifacts  产物版本（draft / final）
GET  /v1/approvals                   待审批列表
GET  /healthz
```

## 5. 审批闸门

- run 进入 `waiting_approval` 后，工作流阻塞在一个 `threading.Event` 上；
- `POST /approve` 先落库（approvals 表），再唤醒闸门；
- 超时（默认 600s）按**拒绝**处理，并把仍挂着的审批记录结为 `timeout`；
- 批准人、意见、时间全部入事件流，供审计导出。

这是「审批是一等状态」的最小实现：监控能看到「卡在等人」，而不是「在跑但很慢」。

## 6. 产物

| 产物 | 状态 | 说明 |
| --- | --- | --- |
| `report.md` | draft | 首次生成，供审批查看 |
| `assessment.json` | draft | 结构化判定（路径/理由/材料/引用） |
| `report.final.md` | final | 审批通过后生成；拒绝则不生成 |

每个产物带 sha256 与版本号；`artifacts` 表可按 run 追溯。

## 7. 与 worker（DSH）的关系

当前 S1 用的是**确定性 Agent**：直接调用 `domain.banking_a` 的工具完成评估。
这是刻意的——先把「审批 + 事件 + 产物」跑通，再接模型。

接 [spike/dsh-worker](../spike/dsh-worker/README.md) 时只需替换一个执行器：

```
Orchestrator ──submit──▶ worker /v1/runs（task = 评估指令 + case_id）
     ▲                        │
     └──── SSE 事件回传 ◀──────┘
     ▲                        │
     └── worker 的 approval_url ┘（指向 /v1/assessments/{id}/approve）
```

契约不变：worker 的 `run.succeeded` 映射为 `assessment.completed`，
`approval.requested` 映射为 `waiting_approval`。

## 8. 已知限制

| 限制 | 影响 | 下一步 |
| --- | --- | --- |
| 单进程线程池 | 不能水平扩 | 换队列 + 独立 worker（Phase 2） |
| 无鉴权 | 仅内网/演示 | 接 OIDC + 租户校验（Phase 2） |
| 审批闸门在内存 | 进程重启丢等待 | 审批状态入库后由恢复任务重放 |
| 产物存本地目录 | 无对象存储/签名 URL | 换 Artifact Service |
| 只有 v1 规则 | 未接 v2 回归 | 提交参数加 rules_version，评测跑双版 |

## 9. 自测

1. 为什么「引用不合法」要直接 failed，而不是给一个「大概对」的结论？
2. `waiting_approval` 为什么必须是可查询状态，而不是让 HTTP 请求一直挂着？
3. 拒绝后为什么不允许保留 final 产物？草稿要不要保留？
4. 如果审批人 10 分钟没响应，你认为应该自动拒绝还是自动通过？为什么？
5. 接真实 worker 时，哪些事件需要重新映射？契约的哪一层不该变？
