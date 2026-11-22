# 10 · 学习路径与自测总表

> 这份文档把「读」和「做」排成一条可执行的路线，并给出验收标准。
> 全部内容都基于 [智能体漫游](https://agent.jeffjade.com/) 的两个专题 +
> 本仓库的可运行实现。

## 1. 四周路线（每周 4–6 小时）

### 第 1 周：循环与工具（能跑起来）

| 天 | 读 | 做 | 验收 |
| --- | --- | --- | --- |
| 1 | [00 全景](00-harness-architecture.md) | `python -m mini_harness demo` | 能说清 4 轮里每轮发生了什么 |
| 2 | [01 循环](01-agent-loop.md) | `python labs/lab01_loop.py` | 能默写 8 行循环 + 4 种停止条件 |
| 3 | [02 工具](02-tools-protocol.md) | 给 `tools.py` 加一个 `word_count` 工具 | 模型能调用它并拿到结果 |
| 4 | 附录 [cc-p3](appendix/cc-p3.md)（代理循环/Plan Mode） | `--mode plan` 跑一次 | 观察到写工具从 schema 消失 |
| 5 | 附录 [hermes-p2](appendix/hermes-p2.md)（工具系统） | 读 Hermes 工具注册的设计 | 能说出「自注册」的收益与代价 |

### 第 2 周：权限与上下文（能安全地跑）

| 天 | 读 | 做 | 验收 |
| --- | --- | --- | --- |
| 1 | [03 权限](03-permissions-sandbox.md) | `python labs/lab02_permissions.py` | 能解释 deny→ask→allow |
| 2 | 附录 [cc-p9b](appendix/cc-p9b.md)（沙箱/安全） | 给自己的项目写 3 条 deny 规则 | 规则能真的拦住 |
| 3 | [04 上下文](04-context-memory.md) | `python labs/lab03_context.py` | 能解释孤儿 tool 消息 |
| 4 | 附录 [cc-p4](appendix/cc-p4.md)（项目记忆） | 写一份 `AGENTS.md` | 新会话能读出约定 |
| 5 | [08 可观测](08-observability-cost.md) | `python labs/lab07_trace_cost.py` | 能从轨迹定位一次拒绝 |

### 第 3 周：记忆、技能、多代理（能变聪明）

| 天 | 读 | 做 | 验收 |
| --- | --- | --- | --- |
| 1 | [04 §3 冻结快照](04-context-memory.md) | `python labs/lab04_memory.py` | 能解释「说完立刻记住」的取舍 |
| 2 | [05 Skill](05-skills.md) | `python labs/lab05_skills.py` | 能写一个合格的 SKILL.md |
| 3 | 附录 [cc-p5](appendix/cc-p5.md)、[hermes-p3](appendix/hermes-p3.md) | 把上周的一个流程沉淀成 Skill | 新会话索引里出现它 |
| 4 | [06 子代理](06-subagents-multiagent.md) | `python labs/lab06_subagent.py` | 能写出自包含的 goal |
| 5 | 附录 [cc-p6](appendix/cc-p6.md)（Skill 体系） | 读社区 Skill 目录 | 能判断哪些该做成工具 |

### 第 4 周：多入口、成本、落地（能上线）

| 天 | 读 | 做 | 验收 |
| --- | --- | --- | --- |
| 1 | [07 扩展](07-mcp-plugins-gateway.md) | 设计一个 Gateway 会话 key | 能说清为什么要 topic |
| 2 | 附录 [hermes-p3](appendix/hermes-p3.md)（网关/看板） | 画一条消息进 Agent 的链路 | 5 个模块名不卡 |
| 3 | [08 成本](08-observability-cost.md) | 给任务集加 3 个验收 | 能跑出通过率/轮次/token |
| 4 | [09 安全与组织](09-security-and-org.md) | 过一遍落地清单 | 至少发现 2 个缺口 |
| 5 | 附录 [hermes-p4a](appendix/hermes-p4a.md)（架构拆解） | 对照本仓库画自己的架构图 | 能标出每个部件的替换成本 |

## 2. 章节 → 文档 → 代码 总映射

### Claude Code 专题（45 篇）

| 部分 | 章节数 | 分册 |
| --- | --- | --- |
| 一 基础认知 | 2 | [cc-p1](appendix/cc-p1.md) |
| 二 快速上手 | 6 | [cc-p2a](appendix/cc-p2a.md) · [cc-p2b](appendix/cc-p2b.md) |
| 三 工作原理 | 2 | [cc-p3](appendix/cc-p3.md) |
| 四 项目记忆 | 5 | [cc-p4](appendix/cc-p4.md) |
| 五 高级扩展 | 6 | [cc-p5](appendix/cc-p5.md) |
| 六 Skill 体系 | 3 | [cc-p6](appendix/cc-p6.md) |
| 七 实战与最佳实践 | 8 | [cc-p7a](appendix/cc-p7a.md) · [cc-p7b](appendix/cc-p7b.md) |
| 八 反思与进阶 | 2 | [cc-p8](appendix/cc-p8.md) |
| 九 进阶实践 | 9 | [cc-p9a](appendix/cc-p9a.md) · [cc-p9b](appendix/cc-p9b.md) |
| 十 排障速查 | 2 | [cc-p10](appendix/cc-p10.md) |

### Hermes Agent 专题（17 篇）

| 部分 | 章节数 | 分册 |
| --- | --- | --- |
| 一 入门 | 3 | [hermes-p1](appendix/hermes-p1.md) |
| 二 核心机制 | 3 | [hermes-p2](appendix/hermes-p2.md) |
| 三 实战 | 5 | [hermes-p3](appendix/hermes-p3.md) |
| 四 进阶 | 6 | [hermes-p4a](appendix/hermes-p4a.md) · [hermes-p4b](appendix/hermes-p4b.md) |

## 3. 机制 → 代码 → 实验 对照

| 机制 | 实现 | 实验 | 关键断言 |
| --- | --- | --- | --- |
| 代理循环 | `loop.Agent.run` | lab01 | 4 种停止条件 |
| 工具注册与执行 | `tools.ToolRegistry` | lab01/02 | 错误进对话 |
| 规则与模式 | `policy.PermissionPolicy` | lab02 | deny→ask→allow |
| 沙箱 | `policy` + `tools.resolve_in_workspace` | lab02 | 越界不落地 |
| 上下文压缩 | `context.ContextManager` | lab03 | 压缩后仍配对 |
| 记忆冻结 | `memory.MemoryStore` | lab04 | 同会话 system 不含新记忆 |
| 技能渐进披露 | `skills.SkillManager` | lab05 | 索引无正文 |
| 子代理隔离 | `loop.Agent.delegate_run` | lab06 | 父上下文无子步骤 |
| 轨迹与账本 | `trace.Tracer` | lab07 | 轨迹 token == 账本 |
| 事件溯源 / 取消 | `runtime.Runtime` | lab08 | 崩溃后重建、取消穿透 |
| 工具网关 / 审批 / 角色 | `runtime.ToolGateway` / `ApprovalBroker` | lab09 | 只读重试、审批留痕 |

## 4. 自测总表（40 题）

按主题各取 5 题，答不上来就回到对应文档的小节。

**循环（文档 01）**
1. 一轮包含哪些步骤？
2. 五种停止出口分别是什么？
3. 为什么写操作不能并行？
4. Plan Mode 为什么要在两层拦？
5. 「模型不认输」在工程上意味着什么？

**工具（文档 02）**
6. 工具的四个组成部分？
7. 为什么 handler 异常要变成 tool 消息？
8. 截断为什么保留尾部？
9. 自注册的代价是什么？
10. `edit_file` 为什么要先读？

**权限（文档 03）**
11. 三层闸门分别防什么？
12. 规则求值顺序？
13. 沙箱能替代规则吗？
14. `acceptEdits` 下什么仍会弹窗？
15. 灾难性命令黑名单的真实价值？

**上下文与记忆（文档 04）**
16. 什么会破坏前缀缓存？
17. 冻结快照的代价是什么？
18. 压缩要保留哪三段？
19. 孤儿 tool 消息怎么产生？
20. 记忆不该写什么？

**Skill（文档 05）**
21. L0/L1/L2 分别是什么？
22. 什么该做成工具而不是 Skill？
23. Skill 的信任边界为什么等同于代码？
24. 新 Skill 为什么下个会话才生效？
25. Curator 解决什么问题？

**多代理（文档 06）**
26. 子代理的本质是什么？
27. 什么时候不该派子代理？
28. goal 必须包含哪四段？
29. 深度上限为什么是 1？
30. 多个子代理改同一文件怎么办？

**扩展（文档 07）**
31. MCP 与工具函数的本质区别？
32. Gateway 会话 key 为什么含 topic？
33. Cron 任务的冷启动意味着什么？
34. API Server 公网暴露等于什么？
35. 插件与 MCP 的风险差异？

**可观测与安全（文档 08/09）**
36. 成本大头是什么？为什么？
37. 三种破坏缓存前缀的写法？
38. 提示注入为什么权限层只能缓解？
39. 「Agent 会自信地错」的工程对策？
40. 落地清单里哪一条最容易被忽略？

## 5. 学完之后的三个练习

1. **替换模型**：把 `DemoLLM` 换成真实 API（`--provider openai`），跑同一个任务，
   对比轮次、token、失败模式。真实模型会更啰嗦还是更简洁？
2. **加一个工具**：实现 `run_tests`（只跑白名单命令），并给它写权限规则与实验。
   体会「工具越专用，规则越好写」。
3. **做一次事故复盘**：故意让 Agent 陷入「读文件 → 觉得不对 → 再读」的循环，
   用轨迹定位，然后只改一处（提示词/停止条件/权限）把它修好。

## 6. 参考资料

- 站点：[智能体漫游](https://agent.jeffjade.com/) ·
  [Claude Code 专题](https://agent.jeffjade.com/claude-code/) ·
  [Hermes Agent 专题](https://agent.jeffjade.com/hermes-agent/)
- 官方文档：[Claude Code](https://code.claude.com/docs/) ·
  [Hermes Agent](https://hermes-agent.nousresearch.com/docs/)
- 本仓库：`docs/appendix/` 是章节精读笔记，`mini_harness/` 是可运行实现，
  `labs/` 是可验收实验。

> 说明：附录分册是「学习笔记」，不是官方文档的翻译；版本、命令、价格等信息请以官方为准。
