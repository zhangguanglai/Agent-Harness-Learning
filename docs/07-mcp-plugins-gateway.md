# 07 · 扩展与多入口：MCP、插件、Gateway、Cron

> 对应站点：[MCP 协议](https://agent.jeffjade.com/claude-code/mcp/)、
> [Plugins 插件](https://agent.jeffjade.com/claude-code/plugins/)、
> [消息网关（Hermes）](https://agent.jeffjade.com/hermes-agent/messaging-gateway/)、
> [Routines 与定时自动化](https://agent.jeffjade.com/claude-code/routines-automation/)、
> [架构拆解 · API Server](https://agent.jeffjade.com/hermes-agent/architecture-deep-dive/)。
> 代码：本仓库用 `cli.py` 演示最小入口层；扩展机制以文档 + 附录分册为主。

## 1. 一个内核，多种入口

Hermes 的架构原则里最值钱的一条：**platform-agnostic core**。

```
CLI ─┐
Gateway（Telegram/Slack/…）─┤
Cron ─┼──→ 同一个 AIAgent 循环 ──→ 工具 / 权限 / 记忆
HTTP API ─┤
ACP/IDE ─┘
```

如果 Telegram 的分支逻辑写进了循环体，你就有了两个 Agent。正确做法是：

- 循环只处理「消息列表 + 工具」；
- 平台差异在 **adapter** 层翻译（消息格式、鉴权、会话 key、Markdown 渲染）；
- Slash 命令等交互能力放在共享注册表里（Hermes 的 `COMMAND_REGISTRY`）。

## 2. MCP：把外部能力标准化成工具

MCP（Model Context Protocol）解决的是「工具生态」问题：

| 维度 | 没有 MCP | 有 MCP |
| --- | --- | --- |
| 接入新系统 | 为每个系统写一个工具 | 写一个 MCP server |
| 复用 | 绑定在你的 Harness 里 | 任何支持 MCP 的客户端可用 |
| 权限 | 你自定义 | server 暴露的能力边界即工具面 |
| 进程隔离 | 无 | stdio / SSE / HTTP，独立进程 |

关键认识：**MCP 不是「更好的函数调用」，而是「工具的服务化」**。
它带来三个工程后果：

1. 工具列表变成动态的（运行时连接），所以 schema 要在会话开始时定型；
2. 每个 server 映射为一个 toolset（`mcp-<name>`），便于按需启用；
3. 子进程的环境变量必须白名单，**不能把宿主的 `OPENAI_API_KEY` 漏给它**。

## 3. 插件：内核的稳定扩展点

插件与 MCP 的区别：

| | MCP | 插件 |
| --- | --- | --- |
| 扩展什么 | 工具（能力） | 工具 + 命令 + Hook + 渲染 |
| 运行位置 | 独立进程 | 宿主进程内 |
| 权限 | 进程边界 | 与宿主同权，**风险更高** |
| 适合 | 外部系统集成 | 深度定制产品行为 |

Hermes 的插件分三类，其中 memory provider 与 context engine 是**单选**的
（一个 profile 只能启用一个），因为它们是替换核心组件，不是叠加能力。
这个区分很重要：**「扩展能力」可以多，「替换组件」必须唯一**。

## 4. Gateway：把 Agent 接到消息平台

一条消息进入 Gateway 后的典型链路：

```
平台 webhook → adapter.on_message() → 鉴权（allowlist/pairing）
    → 解析 session key（platform + chat + topic）
    → 加载历史 → 会话卫生（超长先压缩）
    → AIAgent.run_conversation()
    → 格式化回复（平台 Markdown 差异）
    → 投递
```

四个必须做对的点：

1. **默认拒绝**：未授权用户 id 一律不理，不要「先回一句再判断」；
2. **会话 key 稳定**：私聊、群聊、话题要能区分，否则历史会串；
3. **并发锁**：同一会话两条消息同时到达，要串行处理（per-session lock）；
4. **回复格式**：Telegram 的 Markdown 与 Slack 不同，格式化放在 adapter。

站点里 Gateway 还有一个细节：进 Agent 前做「会话卫生」（如 85% 阈值先压缩），
避免长会话直接撞 API 上限。

## 5. Cron：定时任务也是 Agent 任务

```
Scheduler tick → 读取 jobs.json → 到期任务
    → 新建 AIAgent（通常无历史）
    → 注入任务 Skill 上下文
    → 执行 → 投递到目标平台 → 更新 next_run
```

与 shell crontab 的区别：

- 任务是自然语言目标，需要 Agent 判断怎么做；
- 每次都是**冷启动**（无对话历史），所以依赖记忆/Skill 而非上下文；
- 失败要能重试且有幂等边界（别让「发日报」发两遍）。

设计检查：job 的 prompt 必须自包含 + 有明确的投递目标 + 有失败告警。

## 6. HTTP API：把 Agent 变成 OpenAI 兼容后端

Hermes 的 API Server 暴露 `/v1/chat/completions`、`/v1/responses` 等端点，
让 Open WebUI / LobeChat 这类前端直接接进来。

三个安全事实：

- 非回环绑定（`0.0.0.0`）时**必须**设 `API_SERVER_KEY`；
- 这等于把完整 toolset（含 `terminal`）暴露到网络——**公网暴露 = 远程 shell**；
- 多用户场景用 profile 隔离（不同 home、不同端口、不同 key）。

前端传来的 `system` / `instructions` 是**叠加**在核心系统提示之上的，
这带来一个微妙的注入风险：用户可以通过 system 字段覆盖行为约束。
缓解方式是「核心约束在后、用户内容在前」，并在权限层做兜底。

## 7. Profile：隔离的最小单位

```
hermes -p work   → ~/.hermes-work/   （独立 home、会话、Gateway PID）
hermes -p life   → ~/.hermes-life/
```

用 profile 而不是「同一个 home 里加命名空间」，原因：

- 配置、记忆、会话、凭证全部隔离，不会串；
- 两个 profile 可以并行跑 Gateway，互抢 bot token 的问题也更容易发现；
- 删除/迁移一个 profile 是原子操作。

本仓库对应的是 `--memory-home` / `--skills-root` / `--workspace` 三个参数——
同一台机器上跑多份实验互不干扰。

## 8. 扩展选型决策表

| 需求 | 选择 | 理由 |
| --- | --- | --- |
| 接一个内部 REST API | 写工具（或 MCP server） | 逻辑简单，进程内即可 |
| 接一个第三方工具生态 | MCP | 复用现成 server，进程隔离 |
| 改记忆/压缩策略 | 插件（替换组件） | 需要替换核心实现 |
| 加一个只在该产品出现的命令 | 插件（命令） | 与平台适配绑定 |
| 接 IM | Gateway adapter | 只做消息翻译 |
| 每天定时巡检 | Cron job | 冷启动 + 投递 |
| 给别的 UI 用 | HTTP API | OpenAI 兼容最省事 |

## 9. 自测

1. 为什么平台分支不能写进循环体？写进去会发生什么？
2. MCP 和「多写几个工具函数」的本质区别是什么？
3. Gateway 的 session key 为什么必须包含 topic/thread？不包含会怎样？
4. Cron 任务为什么强调「冷启动」？这对 prompt 写法有什么要求？
5. 把 API Server 绑到 `0.0.0.0` 而不设 key，等价于什么？
6. 什么该做成插件，什么该做成 MCP？举一个各自的反例。
