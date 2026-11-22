# 15 · 方案 C 技术设计：垂直领域 Agent 工作台

> 定位：**垂直领域的 Agent 工作台**——用户带着「案件/项目/工单」进来，Agent 在其工作区里
> 用领域工具与知识库完成产出（文档、表格、检查项、报告），关键动作经人工审批，全过程可审计。
>
> 基座：DeepSeek Harness（DSH）作为**运行时**；控制面自研。
> 选型依据见 [14 平台选型](14-harness-platform-selection.md)。

## 0. 假设与非目标

本文先固定一组假设，任何一条不成立都要回来改设计（你可以直接指出要改的那条）：

| # | 假设 |
| --- | --- |
| A1 | 用户以「工作区/案件」为单位协作，一个案件可有多轮任务与多个交付物 |
| A2 | 交付物是**文件**（docx/xlsx/pptx/pdf）与结构化结论，不是代码 PR |
| A3 | 存在领域知识库（法规、SOP、历史案例），回答必须带引用 |
| A4 | 关键动作（对外提交、删除、批量修改）需要人工审批，审批要留痕 |
| A5 | 形态是 SaaS 多租户，同时支持私有化部署（同一套镜像，换配置） |
| A6 | 单租户峰值 50 并发 run，平台目标 500 并发（可水平扩） |
| A7 | 模型以 DeepSeek 为主，允许按租户配置其他 OpenAI 兼容模型 |
| A8 | 团队 6–8 人（平台 2、Worker/DSH 2、领域工具 2、前端 1、SRE/QA 1） |

**非目标**：通用 IDE 编码 Agent、公开的 Agent 市场、模型训练/微调、无审批的全自动对外执行。

---

## 1. 架构总览

```
┌──────────────────────── 控制面（自研，多副本） ────────────────────────┐
│  Gateway（REST+SSE, OIDC, 租户, 限流）                                  │
│  Orchestrator（队列, 状态机, 租约, 重试, 取消, 配额）                    │
│  Session/Event Service（追加事件, 投影, 保留, 导出）                     │
│  Approval Service（策略, 工单, SLA, 双人审批, 审计）                     │
│  Model Gateway（路由, 降级, 预算, 缓存, 密钥）                            │
│  Knowledge Service（摄取, 检索, 引用）  Artifact Service（产物版本）      │
│  Eval & Observability（任务集, 指标, 成本账本）                           │
└───────────────┬──────────────────────────────────┬─────────────────────┘
                │ 调度（HTTP/JSON）                 │ 事件拉取（SSE）
                ▼                                   │
┌──────────────────── Worker Pod（每 run 一个容器） ─────────────────────┐
│  dsh-worker（Node）：HTTP API + 生命周期 + OTel                          │
│   └─ DSH（headless/SDK bundle） + 领域 bundle                            │
│       ├─ 领域工具：kb.search / artifact.write / checklist.run …          │
│       ├─ MCP server sidecar（可独立部署、按租户配置）                     │
│       ├─ preset：领域身份 + system prompt + skills                       │
│       └─ permission-presets：工具白名单 + 需审批动作                      │
│  工作区卷（rw）· 只读根文件系统 · 默认禁网（仅放行模型网关与 MCP）          │
└──────────────────────────────────────────────────────────────────────────┘
```

数据流（一次任务）：

```
用户 → Gateway → Orchestrator 建 run/事件 → 调度 worker
worker → DSH 循环 → 工具调用（领域工具/MCP/文件）
worker → 事件 SSE → Session Service 落库 → Gateway 推给前端
需要审批 → worker 调 Approval Service → 阻塞 → 批准后继续
结束 → 产物写 Artifact Service → 审计与成本入账
```

---

## 2. 十条关键决策（ADR）

| # | 决策 | 理由 | 代价 |
| --- | --- | --- | --- |
| D1 | **每 run 一个容器 worker**，DSH 不承载多租户 | 桌面/单机假设与 SaaS 冲突；进程边界即安全边界 | 冷启动开销，需要预热池 |
| D2 | **控制面拥有会话**，worker 只产事件 | 崩溃可恢复、审计唯一来源、跨副本一致 | 事件要可靠传输（SSE + 落库 + 断点） |
| D3 | Worker 对外用 **HTTP/JSON**，内部用 DSH SDK 的 JSON-RPC/stdio | 不把 stdio 暴露给控制面；sidecar 负责翻译 | 多一层 adapter |
| D4 | **审批外部化**：DSH 的 ask 转控制面工单 | 双人审批、SLA、移动端审批、审计 | 需要失败关闭与超时策略 |
| D5 | 领域能力优先做成 **MCP server**，深度集成才写 DSH tool 包 | 解耦、可独立升级、可被其他 Agent 复用 | MCP 协议开销与版本管理 |
| D6 | **模型统一走 Model Gateway** | 密钥不落 worker、预算与路由集中、可审计 | 多一跳，需要流式透传 |
| D7 | **事件为事实来源**，run 状态是投影 | 恢复=重放；审计=同一条流 | 事件表增长，需要归档 |
| D8 | **副作用幂等键** = `run_id + tool_call_index` | 重试不重复写文件/发消息 | 工具必须支持幂等键 |
| D9 | 五类版本化：worker 镜像、bundle、preset、prompt、工具 schema | 出问题能精确定位与回滚 | 版本矩阵要管 |
| D10 | 私有化与 SaaS **同一套镜像 + 配置差异** | 避免两套代码 | 配置面要设计好 |

---

## 3. 组件设计

### 3.1 Gateway（接入层）

职责：认证、租户解析、限流、请求校验、SSE 转发。**不做业务编排**。

- 认证：OIDC Authorization Code + PKCE；服务间用 mTLS + 短期 token；
- 租户：从 token claim `tenant_id` 取，**不信任请求体**；
- 限流：按租户 + 用户双层令牌桶；run 提交额外受并发配额约束；
- 幂等：`Idempotency-Key` 头 → 24h 内同 key 返回同一 run。

### 3.2 Orchestrator（编排层）

**状态机**（终态不可逆）：

```
queued ──lease──▶ running ──▶ succeeded
  │                 │  ▲
  │                 │  └── approval ──▶ waiting_approval ──(批准)──┘
  │                 ├──▶ failed
  │                 ├──▶ cancelled
  │                 └──▶ timed_out
  └──(配额/取消)──▶ cancelled
```

- 队列：Postgres `FOR UPDATE SKIP LOCKED`（10k run/天以内够用）；更高吞吐换 Redis Streams；
- 租约：worker 每 5s 心跳；30s 无心跳 → 丢弃租约 → 重新排队（`attempts+1`）；
- 重试：只重试**基础设施失败**（worker 崩溃、调度失败），不重试任务语义失败；
- 取消：先发 cancel 给 worker，10s 未终态 → 强杀容器 → 标记 `cancelled`；
- 配额：租户级并发、日 token、日运行数，超限 429 + `Retry-After`；
- 调度：按租户亲和 + 镜像版本 + 预热池选择 worker 节点。

### 3.3 Session/Event Service

- **只追加**事件表（见 §4.3），`seq` 由服务端分配；
- 投影：`messages`、`tool_calls`、`artifacts` 三张读模型表，由消费者异步更新；
- 保留：热 90 天在 Postgres；冷数据归档到对象存储（Parquet/JSONL），按租户删除；
- 导出：审计导出为不可变包（含哈希链），支持监管取证；
- 压缩：交给 DSH 的 compaction 事件，控制面只记录「压缩发生」与前后 token。

### 3.4 Worker 运行时

镜像内容：Node 24 + DSH（锁定版本）+ 领域 bundle + `dsh-worker` sidecar + OTel collector（可选）。

```
dsh-worker（HTTP :8080）
  ├─ POST /v1/runs            启动一个 run（幂等：同 run_id 重复调用返回 409/同一 handle）
  ├─ GET  /v1/runs/{id}/events SSE（after 游标）
  ├─ POST /v1/runs/{id}/cancel
  ├─ GET  /healthz, /readyz
  └─ 内部：spawn DSH（stdio JSON-RPC）→ 翻译事件 → 落内存 ring buffer + 转发
```

生命周期：

1. 控制面 `POST /v1/runs`（携带 workspace 挂载点、limits、preset、callback）；
2. worker 起 DSH 会话，逐事件转发；
3. 控制面消费 SSE 并落库（单写者），天然去重（`seq`）；
4. 结束：worker 返回终态；控制面核对事件末条；不一致则标记 `failed` 并告警。

**崩溃恢复**：worker 死 → 租约过期 → 新 worker 从 `checkpoint`（DSH session JSONL 快照）
或事件重放继续；重放时副作用工具靠幂等键去重。

### 3.5 DSH 领域 Bundle

一个 npm 包（bundle），通过 profile patch 安装，内容：

| 内容 | 说明 |
| --- | --- |
| `cordis.patch.yml` | 插入/覆盖插件行：工具、preset、权限、OTel |
| `preset` | 领域身份（system prompt）、可用 skill、工具集 |
| tools | 轻量工具（无外部状态）直接写 tool 包；重工具走 MCP |
| permission preset | 只读/可写/需审批三档，映射到审批策略 |
| hooks | 文件写前校验、敏感词、产物命名规范 |
| skills | 领域 SOP（检查清单、报告模板） |

**工具清单（首版）**：

| 工具 | 类型 | 说明 |
| --- | --- | --- |
| `kb.search` / `kb.read` | MCP | 知识库检索，返回 chunk + 引用 id |
| `case.get` / `case.update` | MCP | 案件元数据读写 |
| `artifact.write` / `artifact.read` | MCP | 产物写读（走 Artifact Service，不直接写盘） |
| `doc.render` | tool | 模板渲染 docx/xlsx/pptx |
| `checklist.run` | tool | 领域检查清单，返回结构化结果 |
| `submit_for_review` | tool | 触发人工审批（对外提交前） |

### 3.6 Approval Service

- 策略：`tool + action + risk_level` → 审批链（1 人或 2 人）；
- 形态：站内工单 + IM 通知 + 移动端；
- SLA：默认 4 小时；超时升级到主管；24 小时未处理 → run 标记 `expired`；
- 失败关闭：审批服务不可用时，`ask` 一律拒绝（不自动放行）；
- 审计：谁、何时、看了什么上下文、批了什么，全部入事件流。

### 3.7 Model Gateway

- 统一 OpenAI 兼容入口，worker 只拿短期 token（15 分钟）与网关地址；
- 路由：按租户配置 → 主模型 → fallback 链；按任务类型（规划/抽取/写作）选模型；
- 预算：请求前预扣、请求后结算；超预算熔断并给用户可读原因；
- 缓存：前缀缓存依赖稳定 system（DSH 已保证），结果缓存仅用于确定性任务；
- 审计：记录 token、模型、延迟，入成本账本（不记录完整 prompt 到日志）。

### 3.8 Knowledge Service

- 摄取：PDF/Word/HTML → 解析 → 分块（按标题层级）→ 向量 + 关键词索引；
- 检索：混合召回（BM25 + 向量）→ 重排 → 返回 `chunk_id` 与元数据；
- 引用：Agent 输出必须带 `[^chunk_id]`；未带引用的结论在 UI 上标黄；
- 隔离：租户级 collection，检索强制 `tenant_id` 过滤；
- 更新：文档版本化，旧 chunk 软删除，保证引用可回溯。

### 3.9 Artifact Service

- 内容寻址存储（hash 为 key），元数据在 Postgres（`artifact_versions`）；
- 每次写入生成新版本，保留 diff；审批通过才有「正式版」标记；
- 下载走短期签名 URL；导出包带审计清单。

### 3.10 Eval & Observability（见 §8）

---

## 4. 契约

### 4.1 对外 API（节选）

```http
POST /v1/workspaces/{ws_id}/runs
Authorization: Bearer <oidc>
Idempotency-Key: 9f2c...

{ "task": "核对第 3 份材料与清单 A 的一致性",
  "preset": "compliance@1.2.0",
  "limits": { "max_turns": 24, "token_budget": 200000, "wall_seconds": 900 } }

→ 202 { "run_id": "run_01H...", "status": "queued", "events_url": "/v1/runs/run_01H.../events" }
```

```http
GET  /v1/runs/{run_id}                 状态 + 结果摘要 + 成本
GET  /v1/runs/{run_id}/events?after=42  JSON 或 SSE（Accept: text/event-stream）
POST /v1/runs/{run_id}/cancel          { "reason": "user" }
GET  /v1/approvals?status=pending
POST /v1/approvals/{id}/decide         { "decision": "approve", "comment": "..." }
GET  /v1/artifacts/{id}/versions
GET  /v1/audit/export?from=...&to=...
```

错误约定：`400` 参数、`401/403` 认证授权、`404` 不存在或跨租户、`409` 幂等冲突、
`429` 限流/配额、`503` 调度不可用（带 `Retry-After`）。

### 4.2 Worker 协议

```http
POST /v1/runs
{ "run_id": "run_01H...", "tenant_id": "t_123",
  "task": "...", "preset": "compliance@1.2.0",
  "workspace": { "path": "/workspace", "mode": "rw" },
  "limits": { "max_turns": 24, "token_budget": 200000, "wall_seconds": 900 },
  "model": { "base_url": "https://gw.internal/v1", "token": "short-lived" },
  "callbacks": { "approval_url": "https://cp.internal/v1/approvals", "events_url": "https://cp.internal/..." } }
```

```http
GET  /v1/runs/{run_id}/events?after=N   → text/event-stream；`event: done` 收尾
POST /v1/runs/{run_id}/cancel           → 202；协作式
GET  /healthz                           → { "ok": true, "dsh": "0.2.2-rc.5" }
```

### 4.3 事件信封

```json
{ "event_id": "uuid", "tenant_id": "t_123", "run_id": "run_01H...",
  "seq": 42, "type": "tool_result", "ts": "2026-11-22T05:00:00Z",
  "schema_version": 1, "prev_hash": "sha256:...",
  "payload": { "tool": "kb.search", "call_id": "c_7", "ok": true, "duration_ms": 312 } }
```

类型：`run.queued|leased|started|step|tool_call|tool_result|approval.requested|
approval.resolved|artifact.written|compaction|run.succeeded|run.failed|run.cancelled|run.timed_out`。

### 4.4 数据模型（核心表）

```sql
-- 租户与主体
CREATE TABLE tenants   (id text PRIMARY KEY, name text, plan text, created_at timestamptz);
CREATE TABLE users     (id text PRIMARY KEY, email text UNIQUE, created_at timestamptz);
CREATE TABLE memberships (tenant_id text, user_id text, role text, PRIMARY KEY (tenant_id, user_id));

-- 工作区与运行
CREATE TABLE workspaces (id text PRIMARY KEY, tenant_id text, name text, created_at timestamptz);
CREATE TABLE runs (
  id text PRIMARY KEY, tenant_id text NOT NULL, workspace_id text NOT NULL,
  status text NOT NULL, preset text, attempts int DEFAULT 0,
  idempotency_key text, created_by text, created_at timestamptz,
  started_at timestamptz, finished_at timestamptz, error text,
  UNIQUE (tenant_id, idempotency_key)
);
CREATE INDEX runs_queue ON runs (status, created_at);

-- 事件（只追加）
CREATE TABLE run_events (
  run_id text, seq bigint, tenant_id text, type text, ts timestamptz,
  payload jsonb, prev_hash text, PRIMARY KEY (run_id, seq)
);
CREATE INDEX run_events_tenant_ts ON run_events (tenant_id, ts);

-- 审批 / 产物 / 账本
CREATE TABLE approvals (
  id text PRIMARY KEY, run_id text, tenant_id text, tool text, args jsonb,
  status text, required_approvals int, approved_by text[], created_at timestamptz,
  expires_at timestamptz, decided_at timestamptz
);
CREATE TABLE artifact_versions (
  id text PRIMARY KEY, artifact_id text, tenant_id text, version int,
  hash text, size_bytes bigint, created_by text, created_at timestamptz, approved boolean
);
CREATE TABLE usage_ledger (
  id bigserial PRIMARY KEY, tenant_id text, run_id text, model text,
  input_tokens int, output_tokens int, cost_micros bigint, ts timestamptz
);
```

---

## 5. 关键时序

**提交**：Gateway 校验 → Orchestrator 写 `run.queued` → 调度器租约 → POST worker →
worker 起 DSH → 事件回流 → 终态。

**审批**：tool 命中 `ask` → worker POST 控制面 → `approval.requested` 事件 →
run 状态 `waiting_approval` → 审批人决定 → `approval.resolved` → worker 继续/终止。

**取消**：API 取消 → 状态 `cancelling` → 通知 worker → 10s 强杀 → `run.cancelled`。

**崩溃恢复**：心跳丢失 → 租约回收 → 新 worker 拿 checkpoint → 重放未完成步骤 →
幂等键防重复副作用 → 事件 `seq` 连续校验失败则告警。

**灰度**：按租户/百分比把 `worker_image` + `preset` 版本路由到新版本；对比成功率与成本；
异常一键回滚（改路由表即可）。

---

## 6. 安全设计

| 层 | 措施 |
| --- | --- |
| 身份 | OIDC + 租户 claim；服务间 mTLS；worker token 15 分钟 |
| 执行 | 每 run 容器：非 root、只读根、seccomp、CPU/内存/时长限额 |
| 网络 | 默认禁网；egress 仅放行模型网关与注册的 MCP endpoint；DNS 白名单 |
| 文件 | 工作区卷 rw，其余只读；产物写入走 Artifact Service 审计 |
| 密钥 | Vault/KMS 注入；worker 不持有长期密钥；日志脱敏 |
| 供应链 | 镜像签名 + SBOM；DSH 版本锁定；依赖审计 |
| 审计 | 事件哈希链；审批不可抵赖；导出包可验签 |
| 提示注入 | 外部内容标记为数据；高危工具强制审批；工具结果不做指令 |

---

## 7. 部署与容量

**开发**：docker compose（gateway + orchestrator + postgres + minio + 1 worker）。

**生产（K8s）**：

| 组件 | 副本 | 资源/副本 | 扩缩容 |
| --- | --- | --- | --- |
| Gateway | 3 | 0.5C/512Mi | HPA CPU |
| Orchestrator | 2 | 1C/1Gi | HPA 队列深度 |
| Session/Approval | 2 | 1C/1Gi | HPA |
| Model Gateway | 3 | 1C/1Gi | HPA QPS |
| Worker | 动态 | 1C/2Gi | KEDA 按队列长度；预热池 10% |
| Postgres | 主 + 2 读 | 4C/16Gi | 垂直 + 分区 |
| 对象存储 | 托管 | — | — |

容量估算（A6：500 并发 run）：500 × 1 vCPU = 500 vCPU，按 70% 利用率约 700 vCPU 集群；
按每 run 平均 12 轮、8k 输入/1k 输出 token 估算模型成本，再乘缓存命中折扣。

---

## 8. 可观测与 SLO

| 指标 | 目标 |
| --- | --- |
| API 可用性 | 99.9%（月） |
| 提交 p95 | < 300ms |
| 事件落库延迟 p95 | < 1s |
| worker 启动 p95 | < 8s（预热池命中 < 2s） |
| 任务成功率（评测集） | ≥ 85% |
| 审批响应 p95 | < 4h（业务 SLA，非系统） |
| 单任务成本 | 按领域基线，超基线 2 倍告警 |

追踪：OpenTelemetry（trace: gateway → orchestrator → worker → tool）；日志结构化；
指标按 `tenant_id` 打标签（注意基数，租户多时用聚合）。

---

## 9. 与 DSH 的边界与升级

**只通过三处接触 DSH**：① worker 镜像里的 DSH 版本；② 领域 bundle 的 patch；
③ SDK/headless 的调用面。其余全部自研，保证可替换。

升级节奏：每季度一次；步骤：锁定新版本 → 跑契约测试（事件 schema、工具面）
→ 灰度 5% 租户 → 对比成功率/成本 → 全量。**任何 DSH 行为变化都不应影响控制面契约**。

---

## 10. 自测

1. 为什么「每 run 一个容器」而不是「一个 worker 跑多个租户的 run」？
2. 事件为什么必须由控制面单写？worker 直接写库会带来什么问题？
3. 审批失败关闭与「超时自动拒绝」的区别是什么？各自防住什么事故？
4. 崩溃恢复时，哪些工具可以安全重放？幂等键放在哪一层最合适？
5. 如果 DSH 下个版本改了事件形状，你的哪一层需要改？为什么只改那一层？
