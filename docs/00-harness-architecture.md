# 00 · Harness 全景：一个 Agent 产品到底由什么构成

> 这份文档是整个学习工程的地基。读完你应该能回答：为什么「套一个聊天 API」不是 Agent？
> Harness 的九个部件各自解决什么问题、坏了会有什么症状、本站哪一章讲它、本仓库哪段代码实现它。
>
> 参照：智能体漫游 [Claude Code 专题](https://agent.jeffjade.com/claude-code/) 与
> [Hermes Agent 专题](https://agent.jeffjade.com/hermes-agent/)。

## 1. 一句话定义

**Harness（驾驭层）= 把「模型的一次次输出」变成「一个能交付任务的过程」的全部工程。**

模型只做一件事：给定上下文，输出文字和/或工具调用。剩下的所有事情——
什么时候停、工具怎么执行、出错怎么办、上下文满了怎么办、哪些操作需要人点头、
上次学到的东西存在哪、从哪个入口进来——都由 Harness 负责。

因此一句更实用的判断标准：

> 把模型换成另一个厂商，你的产品还能跑吗？如果答案依赖「重写整个循环」，那你写的
> 不是 Harness，而是一段和模型耦合的业务代码。

## 2. 为什么「聊天 + 提示词」不够

| 维度 | 聊天机器人 | Agent Harness |
| --- | --- | --- |
| 输入 | 用户的一句话 | 任务 + 工作区事实（文件、git 状态、测试输出） |
| 输出 | 一段文字 | 真实副作用（改文件、跑命令）+ 可审查的结论 |
| 过程 | 单次推理 | 多轮：观察 → 行动 → 读结果 → 再观察 |
| 失败 | 答错 | 改错文件、跑错命令、循环不收敛、上下文爆炸 |
| 责任 | 内容责任 | 内容责任 + **执行责任**（权限、沙箱、审计） |

执行责任是分水岭。一旦 Agent 能 `rm`、能 `git push`、能调用你的生产 API，
「提示词里写禁止」就不再是约束，必须有代码层的闸门。这也是本站把
[沙箱](https://agent.jeffjade.com/claude-code/sandboxing/)、
[安全边界](https://agent.jeffjade.com/claude-code/security-permissions/)
放在「进阶实践」而不是「附录」的原因。

## 3. 九个部件（分层图）

```
                        ┌──────────────────────────────┐
   入口层                │ CLI · IDE · Web · Gateway ·  │  同一内核，多入口
                        │ Cron · HTTP API              │
                        └──────────────┬───────────────┘
                                       ▼
                        ┌──────────────────────────────┐
   会话层                │ 会话存储 · 历史回放 · 压缩谱系 │
                        └──────────────┬───────────────┘
                                       ▼
   ┌───────────────────────────────────────────────────────────────┐
   │ 循环层：while 有工具调用 → 执行 → 写回 → 再推理               │
   │   · 停止条件（自然/轮次/预算/拒绝/中断）                        │
   │   · 提示组装（身份 + 工具说明 + 记忆快照 + 技能索引 + 项目约定） │
   └───────┬───────────────┬───────────────┬───────────────┬───────┘
           ▼               ▼               ▼               ▼
      工具层           权限层          上下文层         记忆/技能层
   注册表/模式/     规则·模式·沙箱·   预算/截断/       MEMORY/SKILL
   执行/错误回传     Hooks/审批        压缩/配对清洗    冻结快照/渐进披露
           │               │               │               │
           └───────────────┴───────┬───────┴───────────────┘
                                   ▼
                        ┌──────────────────────────────┐
   扩展层                │ 子代理 · MCP · 插件 · 定时任务 │
                        └──────────────┬───────────────┘
                                       ▼
                        ┌──────────────────────────────┐
   观测层                │ 事件轨迹 · token 账本 · 成本   │
                        └──────────────────────────────┘
```

九个部件、各自的一句话职责与「坏了会怎样」：

| # | 部件 | 职责 | 典型故障症状 | 本站章节 | 本仓库实现 |
| --- | --- | --- | --- | --- | --- |
| 1 | 循环 | 驱动「推理 → 工具 → 回灌」直到可停止 | 停不下来 / 早早放弃 / 死循环 | [代理循环](https://agent.jeffjade.com/claude-code/agent-loop/) | `mini_harness/loop.py` |
| 2 | 工具 | 把模型意图变成受控的真实副作用 | 工具不存在 / 参数错 / 异常炸穿循环 | [工具系统](https://agent.jeffjade.com/hermes-agent/tools-system/) | `tools.py` |
| 3 | 权限 | 决定「能不能做」，回答 allow/deny/ask | 该拦没拦 / 该放不放 / 审批疲劳 | [安全边界](https://agent.jeffjade.com/claude-code/security-permissions/) | `policy.py` |
| 4 | 上下文 | 预算、截断、压缩、配对清洗 | 突然变笨 / 400 / 成本飙升 | [上下文管理](https://agent.jeffjade.com/claude-code/context-management/) | `context.py` |
| 5 | 记忆 | 跨会话保留事实与偏好 | 每次都要重新交代 / 记忆膨胀 | [项目记忆](https://agent.jeffjade.com/claude-code/claude-md/) | `memory.py` |
| 6 | 技能 | 程序性记忆，按需加载 | 重复摸索 / 提示词越写越长 | [Skills](https://agent.jeffjade.com/claude-code/skills/) | `skills.py` |
| 7 | 多代理 | 隔离上下文、并行子任务 | 主上下文被弄脏 / 权限外溢 | [SubAgents](https://agent.jeffjade.com/claude-code/subagents/) | `subagent.py` |
| 8 | 扩展入口 | MCP / 插件 / Gateway / Cron | 工具与命令在各端不一致 | [MCP](https://agent.jeffjade.com/claude-code/mcp/) · [网关](https://agent.jeffjade.com/hermes-agent/messaging-gateway/) | `cli.py` + 文档 07 |
| 9 | 观测 | 轨迹、账本、归因 | 说不清为什么停/为什么贵 | [Token 成本](https://agent.jeffjade.com/claude-code/token-economics/) | `trace.py` |

## 4. 三条设计主线

理解任何 Harness，都可以用这三条主线去切：

### 4.1 稳定性：prompt 前缀必须是「字节级稳定」的

模型的 KV 缓存按前缀匹配。系统提示里塞一个 `datetime.now()`，缓存全废、成本翻倍。
所以成熟 Harness 都做同一件事：**把会变的东西移出系统提示**。

- 记忆写成**启动快照**，会话中改文件不改已发送的 system（本站 [架构拆解](https://agent.jeffjade.com/hermes-agent/architecture-deep-dive/)）；
- 时间戳、会话 id 放在末尾或单独一行，且不每轮刷新；
- 工具集变更（切 toolset）默认「下一个会话生效」，避免中途换 prefix。

代码对照：`Agent._build_system()` 只在 `run()` 开始时调用一次；`Tracer` 记录
`system_hash`，实验里可以断言同一会话内哈希不变。

### 4.2 安全性：闸门要在代码里，不在提示词里

三层闸门，缺一不可：

1. **规则/模式**（应用层）：allow / deny / ask，以及 plan、dontAsk 等模式；
2. **沙箱**（系统层）：路径限制、网络限制、容器隔离；
3. **人工审批**（流程层）：敏感操作逐次确认，或 CI 里一律拒绝。

提示词与 `AGENTS.md` 只能影响「模型想做什么」，不能影响「允许做什么」。

### 4.3 可扩展性：内核稳定，边缘插件化

- 工具**自注册**（import 即注册），不维护中央清单；
- Skill 用 Markdown，Agent 自己能读能写；
- MCP 把外部能力标准化成工具；
- Gateway / Cron / API 都是同一内核的不同入口。

Hermes 的架构文档把这条叫 platform-agnostic core：`AIAgent` 一份，CLI、Gateway、ACP、
Cron 共享；平台差异只允许出现在 adapter 层。

## 5. 两种典型形态

| | Claude Code 型（本地工程） | Hermes 型（长期驻留） |
| --- | --- | --- |
| 场景 | 改代码、跑测试、交付 diff | 常驻服务器、消息平台、定时任务 |
| 上下文 | 仓库 + `CLAUDE.md` | `MEMORY.md` / `USER.md` / Skill 库 |
| 记忆 | 项目约定为主 | 事实 + 偏好 + 程序性技能 |
| 入口 | CLI / IDE / Desktop / Web | CLI / Gateway 20+ 平台 / Cron / API |
| 关键风险 | 误改代码、危险命令 | 越权访问、账号被盗、长期跑偏 |
| 本站入口 | [Claude Code 专题](https://agent.jeffjade.com/claude-code/) | [Hermes 专题](https://agent.jeffjade.com/hermes-agent/) |

两者共享同一套循环与权限心智。学完一个，另一个的 80% 是映射关系——
这也是本站 [心智模型迁移](https://agent.jeffjade.com/claude-code/mental-model-migration/) 那一章的主题。

## 6. 三种读法

### 路径 A：先跑起来（约 30 分钟）

```bash
python -m mini_harness demo     # 看一次完整多轮循环
python labs/verify.py           # 7 个实验，逐个验证机制
```

然后按 `labs/lab01 → lab07` 的顺序读代码，每读一个模块就改一处、跑一次。

### 路径 B：先建立概念（约 2 小时）

按 01 → 09 顺序读本目录的专题文档，每篇末尾的「自测」都能答上来再往下。
遇到具体命令与配置，再跳到 `docs/appendix/` 对应的分册。

### 路径 C：带着任务来（按需）

- 要给团队落地 → [09 安全与组织落地](09-security-and-org.md)
- 要接 IM / 定时任务 → [07 扩展与多入口](07-mcp-plugins-gateway.md)
- 成本失控 → [08 可观测与成本](08-observability-cost.md)
- 总被权限弹窗打断 → [03 权限与沙箱](03-permissions-sandbox.md)

## 7. 术语表（后文统一用词）

| 术语 | 含义 |
| --- | --- |
| 轮（turn） | 一次模型调用 + 它请求的工具执行完成、结果写回上下文 |
| 会话（session） | 从一次任务开始到停止；可跨多轮，可被压缩、可恢复 |
| 工具面（tool surface） | 当前发给模型的工具 schema 集合，按 toolset / 模式过滤 |
| 冻结快照 | 会话开始时读取的记忆文本，会话中不再变化 |
| 渐进披露 | 先给索引，模型需要时再读全文（Skill 的 L0/L1/L2） |
| 闸门（gate） | 代码层的 allow/deny/ask 判定，与提示词无关 |
| 轨迹（trace） | 运行事件的 JSONL 记录，用于回放、归因、计费 |

## 8. 自测

1. 把「模型」换成另一家，你需要改哪几个文件？如果超过两个，说明哪里耦合了？
2. 为什么记忆要冻结快照，而不是每轮把最新的 `MEMORY.md` 拼进 system？
3. 一个 Agent 一直读文件不改代码，你会先查哪三层？分别对应本仓库哪个文件？
4. `bypassPermissions` 模式下还应该保留哪些拦截？为什么？
5. 如果让你删掉九个部件中的一个来「简化产品」，你会删哪个，代价是什么？

> 参考答案散落在 01–09 各篇；第 4 题的答案在 `policy.py` 的 `DANGEROUS_PATTERNS`。
