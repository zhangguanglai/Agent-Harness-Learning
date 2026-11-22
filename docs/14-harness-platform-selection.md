# 14 · 生产级 Agent 应用：三条技术路线的评估与架构选型

> 问题：要做一个生产级 Agent 应用，基座应该选
> **① 自研（扩展 mini_harness）**、**② 基于 DeepSeek Harness 开源底座**、
> **③ 基于其他开源 Harness**？
>
> 本文给出：事实基线 → 加权评分 → 三个候选架构 → 推荐路线与 12 个月实施计划。
> 目标读者是技术负责人，结论需要你用自己的约束条件复核（见文末决策清单）。
>
> 已确认产品形态为「垂直领域 Agent 工作台」后的细化设计见
> [15 方案 C 技术设计](15-option-c-technical-design.md) 与
> [16 实施方案](16-implementation-plan.md)。

## 0. 结论先行

**推荐：以 DeepSeek Harness（DSH）为运行时基座，用插件/bundle 承载领域能力，
自研「控制面」（鉴权、租户、配额、审批工作流、会话与事件存储、评测与成本），
mini_harness 保留为参考实现与评测夹具。**

不做「纯 fork」，也不做「从零自研」。理由压缩成三句：

1. Agent 的难点在**运行时细节**（沙箱、审批、会话投影、压缩、子代理、MCP、遥测），
   DSH 已经把这些做成了可组合的 60+ 包，自研相当于重写 12–18 人月；
2. 产品的差异在**领域控制面**（谁能用、能做什么、怎么计费、怎么审计），
   这部分任何基座都不会替你写，且 DSH 定位是单机/桌面优先，多租户必须自研；
3. 纯 fork 会背上「上游每周都在动」的 rebase 成本（仓库创建 3 个月、仍在 rc），
   **插件面远小于代码面**，升级成本可控。

## 1. 评估方法

评分维度按「生产级 Agent 应用」的真实约束加权，而不是按功能清单：

| 维度 | 权重 | 说明 |
| --- | --- | --- |
| 交付速度（TTV） | 20% | 多久能跑通第一个真实用户任务 |
| 安全与合规 | 20% | 沙箱、审批、审计、密钥、数据边界 |
| 可控性 | 15% | 循环/提示/工具/策略能否按业务改 |
| 可运维 | 10% | 会话持久化、观测、回滚、故障恢复 |
| 扩展与生态 | 10% | 工具、MCP、模型、Skill、社区 |
| 多入口 | 5% | CLI / Web / API / IM |
| 团队匹配与维护成本 | 10% | 语言栈、学习曲线、人力 |
| 锁定与长期风险 | 10% | 上游变更、许可、停更风险 |

评分 1–5，5 最好。分数是**结论的可追溯依据**，不是精确科学；换约束条件要重打分。

## 2. 事实基线

### 2.1 选项 A：扩展 mini_harness（当前仓库）

已验证的能力（可运行、有实验）：

- 循环、停止条件、工具协议、权限三层、上下文压缩、冻结记忆、Skill 渐进披露、子代理；
- Phase 0 运行时：事件溯源、取消、工具网关、审批状态机、角色权限、bench；
- Phase 1 第 ②步：HTTP API + SSE、鉴权、租户隔离、限流。

缺口（对照 [11](11-production-harness.md)）：

| 缺口 | 影响 |
| --- | --- |
| 无容器级沙箱 | 不能对外提供执行能力 |
| 无队列/分布式 worker | 纵向扩，不能横向扩 |
| 无多模型路由/配额/计费 | 成本不可控 |
| 无前端、无 IDE/ACP 集成 | 只能给工程师用 |
| 无 MCP 客户端、无插件市场 | 生态为零 |
| 单人可读的代码量（~5k 行） | 功能每加一层，复杂度指数上升 |

**判断**：mini_harness 的价值是「把机制讲清楚 + 可做评测夹具」，不是「生产基座」。

### 2.2 选项 B：DeepSeek Harness 作为基座

事实（来自本机安装的 `@deepseek-ai/dsh@0.2.2-rc.5` 与 GitHub API，2026-11-22）：

| 项 | 值 |
| --- | --- |
| 仓库 | [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) |
| 许可 | MIT |
| 语言 | TypeScript（Node ≥ 24.9） |
| 定位 | "Everything is a Plugin"（Cordis 插件容器） |
| 规模 | 约 285k stars / 35k forks（创建于 2026-08，仍在 rc） |
| 活跃度 | 当天仍有 push |

已具备的运行时能力（按包名归纳，全部来自安装清单）：

| 域 | 证据（包） |
| --- | --- |
| 代理循环 | `dsh-agent`、`dsh-agent-loop`、`dsh-goal`、`dsh-goal-round-driver` |
| 工具 | `dsh-tool-bash`、`-pwsh`、`-fs`、`-fs-search`、`-web`、`-skill`、`-todo`、`-subagent`、`-workflow`、`-schedule`、`-present`、`-ask-user` |
| 沙箱 | `dsh-sandbox-local`、`dsh-sandbox-policy`、`dsh-fs-sandbox`、`dsh-bash-sandbox`、`dsh-pwsh-sandbox` |
| 审批/权限 | `dsh-user-approval`、`dsh-permission-presets`、`dsh-authorization` |
| 会话 | `dsh-session`、`-persistence-jsonl`、`-projection`、`-projection-cache`、`-query-sqlite`、`-checkpoint-policy` |
| 上下文 | `dsh-compaction-basic`、`-tool-result-pruner`、`-image-offload`、`dsh-token-meter` |
| 多代理 | `dsh-subagent`、`dsh-subagent-spawn/fork-in-process`、`dsh-agent-team`、`dsh-workflow-ptc` |
| 扩展 | `dsh-mcp-client`、`dsh-mcp-resources`、`dsh-plugin-manager`、bundle 机制（24 个 bundle） |
| 观测 | `dsh-otel`、`dsh-session-telemetry-otel` |
| 入口 | `dsh-headless`（一次性运行）、`dsh-sdk-app`（JSON-RPC）、`dsh-acp-app`、`dsh-web-app`（Web UI） |
| 前端 | 大量 `dsh-client-ui-*`（会话、审批、计划、轨迹、任务、技能…） |

**结构洞察**：DSH 用 **bundle = 一组 Cordis 插件行的 patch 层** 来组织产品形态
（base / web / headless / sdk / acp / 实验特性），所以「换入口」不等于「换内核」。

**它不提供什么**（决定了我们必须自研的部分）：

- 多租户：没有 tenant 概念，profile/workspace 是单机语义；
- 计费/配额：只有 token 计量，没有预算与结算；
- 企业审批流：有 approval UX，没有「双人审批 / 工单 / 超时升级」；
- 领域数据面：没有你们的业务工具、知识库、SOP；
- 容器级隔离：sandbox 是策略与本地实现，跨主机部署仍需容器/微虚机；
- 服务化运维：没有多副本、队列、灰度——桌面优先。

### 2.3 选项 C：其他开源 Harness

分四类，代表项目与许可（2026 年公开资料，详见文末来源）：

| 类别 | 代表 | 许可 | 适合 |
| --- | --- | --- | --- |
| 终端编码 Agent | [Codex CLI](https://github.com/openai/codex)、Cline、Aider、OpenCode | Apache-2.0 / MIT | 开发者个人效率 |
| 自治软件工程平台 | [OpenHands](https://github.com/All-Hands-AI/OpenHands) | MIT（core）+ 商业企业版 | Issue→PR 流水线、容器沙箱 |
| MCP 原生通用 Agent | [Goose](https://github.com/block/goose)（已捐给 Linux Foundation Agentic AI Foundation） | Apache-2.0 | 本地工具集成、桌面/CLI/API |
| 编排/应用框架 | LangGraph、OpenAI Agents SDK、PydanticAI、CrewAI、Dify | MIT / Apache-2.0 | 自定义流程、RAG 应用 |

关键差异：**这些项目的「生产化方向」不同**——
OpenHands 偏「沙箱里的自治开发」，Goose 偏「MCP 生态与本地工具」，LangGraph 偏
「确定性编排」，DSH 偏「可插拔的通用 Agent 运行时 + 多入口」。

若你的产品是「垂直领域的 Agent 工作台」（不是纯编码、不是纯编排），
DSH 的抽象层级最接近：工具、技能、审批、子代理、多入口都已经是一等公民。

## 3. 加权评分

| 维度（权重） | A 自研 | B fork DSH | C DSH 插件 + 自研控制面 |
| --- | --- | --- | --- |
| 交付速度（20） | 2 | 4 | **5** |
| 安全与合规（20） | 2 | 4 | 4 |
| 可控性（15） | **5** | 3 | 4 |
| 可运维（10） | 2 | 4 | 4 |
| 扩展与生态（10） | 2 | **5** | **5** |
| 多入口（5） | 2 | **5** | **5** |
| 团队与维护（10） | 3 | 2 | 3 |
| 锁定风险（10） | 4 | 2 | 3 |
| **加权总分** | **2.6** | **3.6** | **4.2** |

打分依据：

- **A 的 2.6**：可控性满分（都是自己的代码），但安全/运维/生态都要从零补，
  时间成本按 [11](11-production-harness.md) 的 Phase 0–2 计约 12–18 人月；
- **B 的 3.6**：功能最全，但 fork 之后每次上游更新都要 rebase 一个 60+ 包的单体仓库，
  团队要吃透 Cordis 与 TypeScript，长期维护成本最高；
- **C 的 4.2**：把「运行时」交给上游，把「产品的控制面」握在自己手里；
  插件面小，升级成本低；代价是受插件 API 约束，且要接受 rc 阶段的变更。

> 注意：如果团队只有 2–3 人且没有 TS 经验，C 的「团队与维护」会掉到 2 分，
> 总分与 A 接近——这时应选 A 或「A 的运行时 + 少量 DSH 参考」，而不是硬上。

## 4. 三个候选架构

### 4.1 方案 A：自研（在 mini_harness 上长）

```
自有前端/API → 自研 Runtime（本次 runtime/） → LocalSandbox → 模型 API
                     ↓
              事件存储 + 评测
```

- 优点：完全可控；代码量小；与现有 Python 技能栈一致。
- 缺点：沙箱、多入口、MCP、前端、子代理、压缩都要自建；每个能力都要自己踩坑。
- 适用：单团队内部工具、任务边界窄、对「可解释性」要求高于功能量。
- 成本：Phase 0 已做（约 2–3 人月）；到 Phase 1 还需 6–8 人月，Phase 2 再 6+ 人月。

### 4.2 方案 B：硬 fork DSH

```
fork 仓库 → 改内核（加 tenant/队列/API） → 自建部署
```

- 优点：改哪里都行；没有 API 约束。
- 缺点：上游每周更新（rc 阶段），rebase 冲突持续存在；测试与发布要自己扛；
  桌面优先的假设（单用户、单机、profile）与 SaaS 冲突，改动会扩散到几十个包。
- 适用：有 5+ 人 TS 团队、且产品形态与 DSH 高度一致（桌面 Agent 工作台）。
- 成本：首版 6–9 人月，之后长期预留 20–30% 人力做上游跟进。

### 4.3 方案 C：插件化混合（推荐）

```
┌────────────── 自研控制面（Python/TS，可水平扩）──────────────┐
│ API Gateway（OIDC、租户、配额、计费）                          │
│ Run Orchestrator（队列、重试、取消、超时、并发）                │
│ Session/Event Store（Postgres + 对象存储）                     │
│ Approval Service（工作流、双人审批、超时升级）                   │
│ Eval & Cost（任务集、回归门禁、token 账本）                     │
└───────────────────────┬──────────────────────────────────────┘
                        │ 每 run 一个沙箱 worker（容器）
                        ▼
┌────────────── DSH Worker（无状态，可替换版本）────────────────┐
│ dsh-headless / dsh-sdk-app + 领域 bundle                      │
│  ├─ 领域工具（自有 tool 包 / MCP server）                      │
│  ├─ permission-presets + approval（接控制面审批）               │
│  ├─ sandbox-policy（路径/命令白名单）                           │
│  ├─ agent preset（领域身份 + system prompt + skill）            │
│  ├─ session JSONL → 事件导出                                    │
│  └─ OTel exporter → 控制面 collector                            │
└───────────────────────────────────────────────────────────────┘
```

**边界原则**：

| 放在 DSH（上游） | 放在控制面（自研） |
| --- | --- |
| 循环、工具执行、上下文压缩、子代理 | 租户、用户、配额、计费 |
| 沙箱策略、审批 UX、会话投影 | 审批工作流、审计导出、合规 |
| 模型适配、MCP、Skill 加载 | 领域数据面、知识库、SOP |
| 前端组件（可选复用） | 产品前端与品牌 |

**为什么不是「全插件」**：DSH 是单机进程模型，把多租户塞进插件会与它的
profile/workspace 假设打架；正确做法是**进程外隔离**（每租户/每 run 一个 worker），
控制面负责路由与配额。

## 5. mini_harness 概念 → DSH 映射

| 我们的实现 | DSH 对应 | 迁移动作 |
| --- | --- | --- |
| `runtime/store.py` 事件 + 投影 | `dsh-session` + `-projection` + `-persistence-jsonl` | 订阅 DSH 会话事件，落自己的库 |
| `runtime/gateway.py` 受控重试 | `dsh-tools` + `-tool-executor-native` | 用 DSH 执行器，重试策略在控制面 |
| `runtime/policy.py` 角色 | `dsh-permission-presets` + `dsh-authorization` | 角色→preset 映射，租户规则在控制面 |
| `runtime/approvals.py` | `dsh-user-approval` | 审批请求转控制面工作流 |
| `mini_harness/sandbox.py` | `dsh-sandbox-policy` + `-fs-sandbox` | 外层再套容器，策略内嵌 |
| `bench/` | 无（自研） | 用 SDK 驱动 DSH worker 跑任务集 |
| `api/` | 无（自研） | 控制面 API，worker 内部用 SDK/JSON-RPC |

**保留 mini_harness 的三个理由**：
① 作为「机制说明书」与新人培训材料；
② 作为 prompt/循环/压缩策略的快速实验场（秒级迭代，不用起 DSH）；
③ 作为「语义基线」——当 DSH 行为可疑时，用 mini_harness 复现最小案例。

## 6. 12 个月路线图

| 阶段 | 周期 | 交付 | 退出条件 |
| --- | --- | --- | --- |
| S0 验证 spike | 2 周 | 在 Linux 容器跑通 `dsh-headless`；写一个含工具 + 审批的 bundle；SDK 起停/取消/事件；OTel 到本地 collector | 5 项全部通过，否则降级 A |
| S1 单租户 MVP | 6–8 周 | 控制面 API、队列、会话存储、容器 worker、领域工具 3 个、评测集 20 条 | 内测用户 20 人，成功率 > 70% |
| S2 多租户 | 8–10 周 | 租户隔离、配额、计费、审批工作流、审计导出 | 跨租户越权 0，成本可归因 |
| S3 规模化 | 持续 | 多副本、灰度、模型路由、缓存、SLO | P95 达标、单位成本下降 30% |

**S0 的验收脚本**（可直接执行）：

```bash
# 1. headless 在容器内跑一个任务（禁网，仅放行模型 API 域名）
docker run --rm --network=none ... dsh-headless --task "列出工作区"

# 2. 安装自定义 bundle，确认工具出现在工具面
dsh --profile prod --dump-config | grep my-tool

# 3. SDK 生命周期
python scripts/spike_sdk.py   # submit → events → cancel → 断言终态

# 4. 审批联动
python scripts/spike_approval.py  # 触发 ask → 控制面批准 → 任务继续

# 5. 遥测
otel-cli span list | grep dsh
```

## 7. 风险登记册

| 风险 | 概率 | 影响 | 缓解 |
| --- | --- | --- | --- |
| DSH 仍是 rc，API 变更 | 高 | 高 | 锁版本；所有接触点收敛到 `worker adapter` 一层；预留 15% 人力跟进 |
| 桌面/单机假设与 SaaS 冲突 | 中 | 高 | 进程外隔离（每 run 容器）；不把租户塞进 DSH |
| TS/Cordis 学习曲线 | 中 | 中 | 先做 spike；bundle 只写领域层，不改内核 |
| 沙箱不是容器级 | 高 | 高 | 外层容器 + 只读根 + 默认无网 + 白名单出网 |
| 上游「官方 bundle」变更影响 | 中 | 中 | 只用稳定包；避免依赖 experimental-* |
| 许可与供应链 | 低 | 中 | MIT 允许 fork；依赖锁定 + SBOM + 私有镜像 |
| 团队规模不足 | 中 | 高 | 若 < 3 人，改选 A，把 DSH 当参考实现 |

## 8. 决策清单（Go / No-Go）

在投入前逐条回答，**任一为否就调整路线**：

1. 产品是「垂直领域 Agent 工作台」而不是纯编码/纯编排吗？（是 → C 合适）
2. 团队能在 2 周内跑通 S0 的五项 spike 吗？（否 → 选 A）
3. 能接受每季度一次上游升级窗口吗？（否 → 选 A 或 B 的锁定分支）
4. 有容器/网络隔离的运维能力吗？（否 → 先解决，否则不要对外提供执行）
5. 多租户与计费是产品必需吗？（是 → 控制面自研，不要指望基座）
6. 领域差异主要在「工具与流程」还是「循环本身」？（前者 → C；后者 → B）

## 9. 自测

1. 为什么「插件化混合」在加权评分上高于「硬 fork」？两个维度分别差在哪？
2. DSH 的 bundle 机制解决了什么问题，又没解决什么问题？
3. 如果把多租户做进 DSH 插件，会和哪些既有假设冲突？
4. S0 spike 的哪一项失败会让你立刻放弃 C？为什么是它？
5. 你的团队规模与语言栈，会让评分表里哪一行的分值改变？改完结论还成立吗？

## 10. 来源

- DSH：本机安装 `@deepseek-ai/dsh@0.2.2-rc.5` 的包清单与 bundle 列表；
  [GitHub 仓库](https://github.com/deepseek-ai/deepseek-harness)（MIT，2026-08 创建）。
- 开源 Harness 概览：[Future AGI 2026 harness 综述](https://futureagi.com/blog/best-agent-harness/)、
  [Stacklok 企业选型](https://stacklok.com/blog/the-8-best-agent-harnesses-for-enterprise-teams-in-2026-ranked/)、
  [OpenHands](https://github.com/All-Hands-AI/OpenHands)、[Goose](https://github.com/block/goose)。
- 本仓库：[11 生产化](11-production-harness.md)、[12 运行时骨架](12-runtime-skeleton.md)、
  [13 HTTP API](13-http-api.md)。

> 说明：评分与成本估算是基于当前信息的判断，不是承诺；上线前请用 S0 spike 校准。
