# 11 · 从 mini_harness 到生产级 Harness：规划与实施

> 前置：[09 安全与组织落地](09-security-and-org.md)、[08 可观测与成本](08-observability-cost.md)。
> 本文回答两个问题：**生产级 Harness 该怎么规划**、**基于本仓库的 mini_harness 必须补哪些东西**。
> 代码对照：[loop.py](../mini_harness/loop.py)、[tools.py](../mini_harness/tools.py)、[policy.py](../mini_harness/policy.py)。
>
> 其中 Phase 0 的六个 PR 已实现为可运行骨架，见 [12 运行时骨架](12-runtime-skeleton.md)。

## 0. 先定义「生产级」，否则会做成一个更大的玩具

生产级不等于功能多，而是**六条可验收的承诺**：

| 维度 | 承诺 | 可验收指标 |
| --- | --- | --- |
| 可靠性 | 单次任务不因进程崩溃而丢状态；失败可重试且不重复副作用 | 任务成功率、恢复成功率、重复副作用次数 = 0 |
| 安全 | 任何副作用都能归因到「谁、在什么权限下、被谁批准」 | 审计覆盖率 100%、越权事件 0 |
| 性能 | 并发可控、时延可预测、有背压 | P95 时延、队列深度、拒绝率 |
| 成本 | 单任务成本可预算、可归因、可优化 | 每任务 token/金额、超预算率 |
| 可运维 | 可灰度、可回滚、可观测、可告警 | 变更回滚时长、MTTR |
| 可扩展 | 工具/模型/入口可插拔且版本化 | 新工具接入工时、破坏性变更次数 |

**判断标准**：如果一条承诺无法用指标验收，它就还不算「生产级」。

## 1. 三条不变量（所有设计都从这三条推导）

1. **每个副作用可归因**：谁（tenant/user/session）、何时、用什么工具、什么参数、谁批准、结果如何。
2. **每次执行可取消、可超时、可重试**：且重试必须幂等，或者明确标记「不可重试」。
3. **每次变更可回滚**：模型、系统提示、工具 schema、Skill、策略，全部版本化，可灰度、可回退。

违反任意一条，都会在规模化后变成事故。先记住这三条，再看模块。

## 2. 生产就绪度自评：mini_harness 现在在哪

| 维度 | 现状 | 生产要求 | 优先级 |
| --- | --- | --- | --- |
| 循环 | 同步单会话，有轮次/token 上限 | 异步、可取消、可恢复、可并发 | P0 |
| 工具 | 进程内执行，超时只杀直接子进程 | 沙箱执行器、进程组回收、PTY、配额 | P0 |
| 权限 | 字符串规则 + 回调审批 | 策略中心（租户/角色/资源）、审批流、审计 | P0 |
| 会话 | 内存 messages，无持久化 | 事件溯源、断点续跑、fork、压缩谱系 | P0 |
| 模型 | 单一 OpenAI 兼容端点 | 多 provider 路由、降级、限流、缓存 | P1 |
| 上下文 | 有损折叠 + 配对清洗 | 精确 tokenizer、分层摘要、provider 缓存策略 | P1 |
| 记忆/Skill | 本地 Markdown + 冻结快照 | 多租户隔离、检索、版本、签名、删除合规 | P1 |
| 多代理 | 同步 delegate，深度 1 | 并发池、预算隔离、结果合并、失败策略 | P2 |
| 可观测 | JSONL 落盘 | 集中采集、采样、脱敏、查询、回放 | P1 |
| 入口 | CLI | HTTP API + 鉴权 + 限流 + 多平台 adapter | P1 |
| 多租户 | 无 | 进程/网络/存储隔离 + 配额 | P2 |

**结论**：mini_harness 的**循环、工具协议、权限语义、压缩与冻结快照**这些「形状」是对的，
可以平移；缺的是**运行时、持久化、隔离、运维**这四层「工程外壳」。

## 3. 目标架构

```
┌───────────── 接入层 ─────────────┐
│ HTTP API · WebSocket · IM adapter │  鉴权 / 租户识别 / 限流
└───────────────┬──────────────────┘
                ▼
┌───────────── 编排层 ─────────────┐
│ Session Service · Run Scheduler   │  会话状态、断点续跑、并发配额
│ 事件总线（run/turn/tool 事件）     │  幂等键、重试策略、死信
└───────────────┬──────────────────┘
                ▼
┌───────────── 执行层（无状态 worker）──────────────┐
│ Agent Runtime：循环 / 上下文 / 权限调用             │
│   ├─ Model Router（多 provider、降级、缓存）        │
│   ├─ Tool Gateway（原生工具 + MCP、schema 版本化）  │
│   └─ Sandbox Executor（容器/Job Object/网络策略）   │
└───────────────┬────────────────────────────────────┘
                ▼
┌───────────── 数据与观测 ─────────────┐
│ 会话存储（事件）· 记忆/技能 · 审计 · 轨迹 · 成本账本 │
└──────────────────────────────────────┘
```

三个关键决策：

1. **执行与编排分离**：worker 无状态，状态在会话存储；这样能水平扩、能重启不丢任务。
2. **沙箱是独立进程边界**：工具（尤其 bash）永远不在 API 进程里执行。
3. **事件是事实来源**：`run_started / turn / tool_call / permission / tool_result / run_finished`
   写入事件存储，状态是事件的投影 —— 断点续跑、审计、回放、计费都从这一条流出来。

## 4. 分阶段路线（每阶段都有退出条件）

### Phase 0：单机可用（2–3 周）

目标：能给自己团队稳定用，不丢任务、能查问题。

| 必做 | 对应扩展 |
| --- | --- |
| 取消 + 超时传播（进程组级） | `Runtime.cancel(run_id)` |
| 会话持久化（SQLite/Postgres + 事件表） | `SessionStore` |
| 结构化错误与重试（区分可重试/不可重试） | `ToolError` 分类 |
| 审计日志（谁批准了什么） | `policy` 事件 |
| 评测集（20 个真实任务 + 验收命令） | `bench/` |

**退出条件**：kill -9 后任务可从事件恢复；同一任务重跑不重复写文件；20 个任务通过率可复现。

### Phase 1：单租户生产（6–8 周）

| 必做 | 说明 |
| --- | --- |
| HTTP API + 鉴权 + 限流 | 取消/流式事件走 SSE 或 WebSocket |
| 容器化沙箱执行器 | 每个 run 一个容器或一个受限 worker 进程 |
| 策略中心 | 租户/角色/工具/参数四元组，策略即代码 |
| 集中式轨迹 + 指标 | OpenTelemetry 或等价方案，采样与脱敏 |
| 成本预算与降级 | 单 run 预算、超限熔断、模型降级链 |
| 灰度与回滚 | 提示/模型/工具版本化，按租户或百分比放量 |

**退出条件**：SLO 达标（成功率、P95、错误预算）；安全评审通过；演练过回滚。

### Phase 2：多租户平台（3–6 个月）

隔离（进程/网络/存储/密钥）、配额与计费、审批工作流、工具市场与签名、
Skill 版本与灰度、租户级评测与报表。

### Phase 3：规模化（持续）

分布式 worker 与调度、跨区域、成本优化（小模型路由、缓存命中率）、
领域专用 agent preset、与 CI/CD 深度集成。

> 经验：**不要跳过 Phase 0 直接做平台**。没有评测集与事件溯源，后面的所有优化都是猜。

## 5. 基于 mini_harness 的必要扩展（模块级）

### 5.1 运行时：从 `Agent.run()` 到 `RunHandle`

现状 `run()` 是同步阻塞，无法取消、无法断点。

```python
# 目标接口（示意）
class Runtime:
    def submit(self, req: RunRequest) -> RunHandle: ...
    def cancel(self, run_id: str, reason: str) -> None: ...
    def events(self, run_id: str) -> Iterator[Event]: ...

@dataclass
class RunHandle:
    run_id: str
    session_id: str
    status: Literal["queued", "running", "waiting_approval", "done", "failed", "cancelled"]
```

要点：取消要传播到模型流、工具进程组、子代理；`waiting_approval` 是**一等状态**，
而不是阻塞在某个回调里。

### 5.2 会话：事件溯源 + 断点续跑

```python
class SessionStore(Protocol):
    def append(self, session_id: str, event: Event) -> None: ...
    def load(self, session_id: str) -> list[Event]: ...
    def fork(self, session_id: str, at_seq: int) -> str: ...
```

- 消息列表是事件的投影，压缩是「写入一条 compaction 事件」，不是覆盖历史；
- 断点续跑 = 从最后一条事件重建 messages；
- 审计与回放共用同一份事件流。

### 5.3 工具：网关 + 沙箱执行器

```python
class ToolGateway:
    def schemas(self, ctx: PolicyContext) -> list[dict]: ...   # 按租户/权限裁剪
    def invoke(self, call: ToolCall, ctx: CallContext) -> ToolResult: ...

class Sandbox(Protocol):
    def exec(self, argv: list[str], *, cwd: str, env: dict, limits: Limits) -> ExecResult: ...
```

- **原生工具与 MCP 走同一网关**，schema 带版本号；
- 工具结果统一 `{"ok", "output", "error", "meta"}`，错误分类（可重试/不可重试/被拒绝）；
- bash 必须经 `Sandbox`，禁止在 API 进程内 `subprocess`；
- `Limits` 至少包含：CPU 时间、内存、输出字节、墙钟超时、网络策略。

### 5.4 权限：从规则表到策略中心

```python
@dataclass
class PolicyContext:
    tenant: str
    user: str
    role: str
    session: str
    approval_chain: list[str]

class PolicyEngine:
    def decide(self, call: ToolCall, ctx: PolicyContext) -> Decision: ...
```

生产要补的四件事：

1. **主体**：不只是「本机用户」，而是租户/用户/角色/服务账号；
2. **审批流**：`waiting_approval` 状态 + 超时策略 + 双人审批；
3. **策略即代码**：策略进 git、可 review、可测试、可灰度；
4. **审计**：决策与批准不可抵赖，和事件流对齐。

### 5.5 模型层：路由 + 降级 + 预算

```python
class ModelRouter:
    def complete(self, req: ModelRequest) -> ModelResponse: ...
    # 内部：按任务复杂度选模型 → 失败按 fallback 链 → 命中缓存直接返回
```

- 输入侧缓存（provider 前缀缓存）要保证 system 字节稳定（mini_harness 已有 `system_hash` 可复用）；
- 输出侧缓存（相同 prompt 的确定性任务）能显著降本；
- 预算：per-run / per-tenant 双上限，超限时降级或中断。

### 5.6 可观测：从 JSONL 到指标

`Tracer` 保留事件形状即可，但落点要变：

| 现状 | 生产 |
| --- | --- |
| 本地 JSONL | 事件总线 + 存储（可查询） |
| 无采样 | 全量审计 + 采样 trace |
| 无脱敏 | 字段级脱敏（密钥、PII） |
| 无告警 | 成功率/成本/延迟指标 + SLO 告警 |

### 5.7 记忆与 Skill：多租户与治理

- 隔离：`tenant/` 前缀 + 存储层强隔离，不允许跨租户读；
- 检索：先用 FTS/关键词，再考虑向量（别一上来就上向量库）；
- 治理：Skill 签名、来源（official/community/private）、版本、依赖校验；
- 合规：删除请求要能定位并清除记忆与轨迹（事件溯源下要设计 tombstone）。

## 6. 十个硬问题（真正决定成败）

| # | 问题 | 典型做法 |
| --- | --- | --- |
| 1 | 取消与超时传播 | context/cancel token + 进程组/job object |
| 2 | 副作用幂等 | 幂等键、写前快照、可回滚操作、去重表 |
| 3 | 断点续跑 | 事件溯源 + 从事件重建上下文 |
| 4 | 沙箱 | 容器/微虚机、只读根、网络白名单 |
| 5 | 并发与背压 | 队列 + 每租户配额 + 拒绝策略 |
| 6 | 成本控制 | 预算、缓存、模型分级、上下文裁剪 |
| 7 | 压缩正确性 | 工具调用配对、provider 差异、摘要质量评测 |
| 8 | 审批体验 | 按前缀授权、批量批准、审批超时 |
| 9 | 提示注入 | 工具结果不可信、高危动作强制审批、数据/指令分离 |
| 10 | 变更管理 | 提示/模型/工具/策略全部版本化 + 灰度 + 回滚 |

## 7. 评测与发布

**没有评测集的生产 Harness 等于闭眼开车。** 最小可用：

1. 任务集：20–50 个真实工单，每个带可执行验收（测试/命令/diff 约束）；
2. 离线跑批：通过率、平均轮次、平均 token、P95 时延；
3. CI 门禁：提示/工具/策略变更必须跑评测，低于基线不允许合并；
4. 线上：金丝雀 + A/B，按租户或流量比例放量；
5. 回归：失败样本进「事故集」，每次修复都加一条。

本仓库的 `labs/verify.py` 是最小形态（机制级断言）；生产要把它换成「任务级评测」。

## 8. 安全与合规清单

- [ ] 执行面隔离：每个 run 独立沙箱，默认无网络，白名单出网；
- [ ] 数据分级：哪些字段可进模型、哪些必须脱敏；
- [ ] 密钥管理：KMS/Vault，工具侧注入，绝不出现在上下文；
- [ ] 审计：工具调用、审批、策略变更三类事件不可篡改；
- [ ] 供应链：插件/Skill/MCP 签名与来源校验；
- [ ] 留存与删除：数据保留期、租户删除、tombstone 传播；
- [ ] 红队：提示注入、越权、路径遍历、SSRF 的固定回归用例。

## 9. 团队与工程实践

| 角色 | 负责 |
| --- | --- |
| 平台/运行时 | 循环、会话、调度、沙箱、模型路由 |
| 工具/生态 | 工具网关、MCP、Schema 版本、SDK |
| 评测/质量 | 任务集、回归门禁、线上指标 |
| 安全/合规 | 策略、审计、红队、数据边界 |

工程习惯：ADR 记录关键取舍；runbook 写清故障处置；每个组件有 SLO；
协议（事件、工具、策略）显式版本化。

## 10. 第一个 sprint 就做的六个 PR

按依赖顺序，全部基于现有 mini_harness：

1. **`Runtime` + 取消/超时**：`run()` 拆成 submit/cancel，bash 改杀进程组；
2. **`SessionStore` + 事件表**：消息改为事件投影，支持从事件重建；
3. **`ToolGateway` 抽象**：把 `ToolRegistry.call` 收口，加错误分类与 schema 版本；
4. **`Sandbox` 接口 + 本地实现**：先抽象，后接容器；bash 只走 Sandbox；
5. **策略上下文**：`decide()` 加 tenant/user/role，审批升级为 `waiting_approval` 状态机；
6. **评测脚本**：`bench/run.py`，跑任务集、输出通过率/轮次/token/时延，接 CI。

这六个 PR 完成后，mini_harness 就具备了「可安全地给一个小团队用」的骨架；
剩下的多租户、分布式、工具市场，都是在这副骨架上加层，而不是重写。

## 11. 自建 Harness 的常见失败

| 失败 | 症状 | 预防 |
| --- | --- | --- |
| 过早抽象框架 | 三个月没跑通一个真实任务 | 先跑通 Phase 0，再抽象 |
| 忽略取消与幂等 | 用户点停止，进程还在跑；重试重复扣款 | 第一版就做 |
| UI 与循环耦合 | 换个入口要改内核 | 内核无 UI 依赖 |
| 只测 happy path | 上线后全是超时/权限/编码问题 | 失败注入 + 事故集 |
| 权限后补 | 事后加权限要改所有工具 | 权限是一等公民 |
| 成本失控 | 一次任务几十美元 | 预算 + 缓存 + 路由 |
| 无版本化 | 改了提示无法回滚 | 变更即版本 |

## 12. 自测

1. 三条不变量分别对应本文哪几个扩展？缺一条会发生什么事故？
2. 为什么「执行与编排分离」是水平扩展的前提？
3. 断点续跑为什么要求事件溯源，而不是定期把 messages 存 JSON？
4. 沙箱执行器与权限规则的分工是什么？为什么 bash 必须走沙箱？
5. 如果你的团队明天要上线，只能做三个 PR，你选哪三个？为什么？
