# Claude Code · 第二部分 快速上手（会话与命令）

> 本分册整理自智能体漫游（agent.jeffjade.com），覆盖章节：
> - 第一个会话（启动你的第一个 Claude Code 会话）— https://agent.jeffjade.com/claude-code/first-session/
> - Slash 命令（Slash 命令与常用功能）— https://agent.jeffjade.com/claude-code/slash-commands/
> - 多平台运行环境全览 — https://agent.jeffjade.com/claude-code/platforms-overview/

## 本册要点

- 工作区以 `claude` 的**启动目录**为锚，在项目根启动才能让 Agent 看见 `package.json`、`CLAUDE.md` 与整体结构。
- `-p`（print）是问完即走的单次查询，可接管道供脚本/CI 使用，代价是没有逐步授权这一层。
- `/clear` 换话题、`/compact` 压同一条任务的长对话，混用会丢细节或让旧任务污染新任务。
- 权限是 Human-in-the-Loop，匹配序为 `deny → ask → allow`；"不再询问"批准的是**命令模式 + 目录**，不是模型的人格。
- CLI、IDE 扩展、Desktop、Web 共用同一套代理循环与 `~/.claude/` 状态，差异只在交互壳层（diff 视图、并行会话、`!` 前缀等）。

## 逐章笔记

### 第一个会话

- **一句话**：把 CLI 从"装好了"推到"能完成一件可验证的小事"，同时建立权限、上下文与工作区三件事的基本心智。
- **机制/要点**：
  - 与网页聊天的根本差别是**工作目录即工作区**：Agent 能读 repo、跑测试、改文件，改文件与跑危险命令前会先问你。启动目录决定它"眼中的项目"边界——在项目根能看见 `package.json` 与整体结构，在 `src/components/` 启动则视野主动缩小。
  - 交互会话（`claude`）与单次查询（`claude -p`）是同一引擎的两种壳层：前者保留多轮上下文并逐步授权；后者 stdin 输入、stdout 输出，因此能接管道与 CI，但遇未授权工具没有交互余地。
  - 会话状态持久化在 `~/.claude/`，所以 `-c` 续当前目录最近一次、`-r <名称|ID>` 按名恢复；退出终端不等于会话丢失。
  - `@路径` 由 CLI 解析后**注入文件内容**；而"请读取 src/foo.ts"只是自然语言请求，是否真调用 Read 由模型决定，大仓库里容易漏读。这是把确定性从模型手里收回到工具层的做法。
  - 权限弹窗把"提议"与"拍板"分离：Read/Grep/Glob 通常不问，Edit/Write 与 Bash 要问；模式分 `default`（首次询问）、`plan`（只读探索）、`acceptEdits`（自动接受工作区内编辑）、`bypassPermissions`（跳过提示，仅限容器/VM）。
- **命令 / 配置 / 代码**：

```bash
cd ~/projects/my-app        # 先进入项目根，工作区以此为锚
claude                      # 交互会话；首次未登录会引导浏览器 OAuth
claude "用三句话概括目录结构和主要技术栈"   # 带开场白启动
claude -c                   # 继续当前目录最近一次会话
claude -r "auth-refactor" "继续上次未完的 PR"   # 按名称或 ID 恢复
claude --model claude-sonnet-4-6   # 走 OpenRouter 时为 anthropic/claude-sonnet-4-6
cat build.log | claude -p "找出导致构建失败的前三个错误及修复建议"
```

```jsonc
// 项目级 .claude/settings.json（个人偏好放 ~/.claude/settings.json）
{
  "permissions": {
    "allow": ["Bash(npm run test *)", "Bash(npm run lint *)"],
    "deny": ["Bash(git push *)"]
  }
}
```

- **坑与边界**：
  - 在深层子目录启动，Agent 看不到根配置，结论容易偏；在 HOME 启动则等于把整个用户目录当工作区。
  - 对 `git push`、`rm`、访问陌生 URL 的 `curl` 习惯性点"永远允许"，是多数事故来源——批准的是该命令模式在该目录可重复执行，不是"以后都不用问"。
  - `-p` 无弹窗通道，未预授权的复杂任务会被直接拒绝，适合只读或已写进 allow 规则的场景。
  - 任务越模糊（"优化项目"）产出越漫无目的；可交付单元应是"修 `UserService.ts` 第 42 行 unused variable"这类。
- **原文**：https://agent.jeffjade.com/claude-code/first-session/

### Slash 命令与常用功能

- **一句话**：以 `/` 开头的输入是发给 CLI 的控制指令，它管"会话怎么跑"，与模型调用的工具分属两个控制面。
- **机制/要点**：
  - 判定条件严格：`/` 必须在**消息最前面**；命令名之后的文字作为**参数**传入，如 `/compact 保留与 auth 相关的决策`、`/export my-session.txt`。
  - 三种机制分工：Slash 命令由你发起（切模型、清上下文、导会话、开计划模式）；内置工具由模型在循环中调用（Read/Edit/Bash/Grep）；bundled skill 由提示驱动，你可用 `/` 触发，模型也会自动选用。
  - 输入 `/` 弹出的列表是**按当前版本、账号、环境过滤**后的结果，支持 `/hel` 式前缀过滤；排查"有没有这个命令"应以面板为准，社区 cheatsheet 常滞后于版本。
  - `/clear` 与 `/compact` 是上下文管理的分水岭：前者清空上下文但会话仍可 `/resume` 找回——"持久化"与"进上下文"是两件事；后者把**同一条任务**的长对话压成摘要，保留继续修同一问题的能力。
  - `/btw` 走旁路短问、不写入主对话历史，几乎不挤占上下文；`/rewind`（别名 `/checkpoint`、`/undo`）则在**对话与工作区**两个维度回到检查点，也可从某条消息起做摘要。
  - 计划链条上没有 `/execute`：批准计划后靠切换权限模式，或由模型调用 `ExitPlanMode` 进入执行段。bundled skill 本质是把一段提示交给模型执行，不是 CLI 里的固定逻辑，因此能随版本换 prompt，也能被你在 `.claude/commands/` 用 Markdown 复刻。
- **命令 / 配置 / 代码**：

```text
第一次进仓库：/init（生成 CLAUDE.md） · /memory · /mcp · /agents · /permissions
任务进行中：  /plan <描述> · /model · /effort · /fast · /context → /compact <焦点> · /btw <问题> · /diff
并行与会话：  /branch（对话分叉，别名 /fork） · /background · /tasks · /resume · /export
提交前打磨：  /diff · /simplify <焦点> · /review · /security-review
出问题：      /rewind · /doctor · /debug · /feedback（/bug）
内置命令：    /clear /model /doctor /permissions —— 编进 CLI，行为固定
bundled skill：/simplify（三路并行审查并改码） /loop（会话开着重跑） /batch（worktree 内并行） /debug
```

```markdown
<!-- .claude/commands/foo.md（项目级）或 ~/.claude/commands/（个人级） -->
---
description: 生成本次改动的摘要
allowed-tools: Read, Grep
---

读取最近一次提交涉及的改动，按影响面输出三点摘要。
```

- **坑与边界**：
  - 任务根本切换了却猛用 `/compact`：摘要丢细节、后续修偏；该 `/clear` 就 `/clear`，必须压缩时也要带焦点说明。
  - 把 `/simplify` 当作提交前唯一审查——它是并行审查而非只读安全审计，漏业务逻辑与产品风险，深度安全仍需 CI 与人工。
  - 假设 cheatsheet 上每条命令都存在：`/vim` 已改为在 `/config` 里切编辑器模式，`/pr-comments` 在较新版本移除。
  - `/branch` 是**对话**分叉，不会替你 `git checkout`；平台与订阅还会裁剪命令，Bedrock/Vertex/Foundry 下部分消费级能力不显示。
- **原文**：https://agent.jeffjade.com/claude-code/slash-commands/

### 多平台运行环境全览

- **一句话**：终端、VS Code/Cursor、JetBrains、Desktop、Web 是同一套代理循环的不同交互壳层，选型看 diff 视图、并行度与 MCP/`!` 的完整度。
- **机制/要点**：
  - 底层一致性来自两处共享：同一套代理循环（工具调用与权限判定），以及同一份 `~/.claude/` 状态目录（会话、记忆、配置）。所以终端与扩展能互相看见会话；两者不一致时首查是否用了不同用户目录或环境变量，而不是怀疑会话丢失。
  - 差异集中在壳层：扩展内置 CLI 但只暴露子集——没有 `!` Bash 前缀、没有 Tab 补全、MCP 只能管已有连接；换来并排 diff、Plan 文档批注、`Alt+K` 插入 `@file#L1-10` 与多 Tab 状态提示。
  - CLI 与 IDE 的粘合点是 `/ide`：外部终端先起 `claude` 再 `/ide` 即挂到当前工作区；在 IDE 集成终端启动则自动挂钩。
  - Desktop 的定位是**会话编排**：同时驱动多个本地并行会话，常与 git worktree 搭配；它与 CLI 共享状态文件，`/desktop`（macOS/Windows）可把当前会话迁过去。
  - Web 版跑在 Anthropic 云沙箱，适合本机装不了 CLI 或从手机派活；与终端通过 `--remote`、`--teleport` 互迁会话。JetBrains 插件则把 IDE 语义喂进上下文：当前选区自动进上下文、`Cmd+Option+K` 插入 `@path#L1-99`、Lint 诊断自动分享。
  - 无官方 GUI 的编辑器（Neovim/Emacs）标准路径是纯终端 + CLI，用 `CLAUDE.md` 与 Skills 固化流程，需要图形 diff 时另开 VS Code/JetBrains 再 `/ide`。
- **命令 / 配置 / 代码**：

```bash
code .        # 从终端启动 IDE，使 GUI 继承 ANTHROPIC_API_KEY 等环境变量
claude        # 在 IDE 集成终端、项目根运行，自动挂钩
```

```jsonc
// VS Code 扩展设置：默认进入只读计划模式（前置条件：VS Code 1.98.0+）
{ "claudeCode.initialPermissionMode": "plan" }
```

```text
决策边界
  要图形 diff、Plan 批注、多 Tab      → VS Code / Cursor 扩展（Spark 图标或命令面板 Open in New Tab）
  团队统一 IDEA 系、要 IDE 内 diff    → JetBrains 插件
  多路并行会话编排，不常开 IDE        → Desktop（配 git worktree）
  SSH、无 GUI、要 ! 与完整 MCP、CI    → 终端 CLI（claude -p）
  本机不能装 CLI，或从手机派任务      → Web（claude.ai/code）
```

- **坑与边界**：
  - GUI 启动的 IDE 拿不到 shell 里的 `ANTHROPIC_API_KEY`，会反复提示登录；用 `code .` 从终端启动以继承环境变量。
  - 扩展能力是子集，需要 `!` 快速 Bash、完整 MCP 配置、Tab 补全或脚本化时，仍要回到集成终端跑 `claude`。
  - `acceptEdits` 自动接受编辑时，Claude 可能改动 IDE 会加载的配置文件；敏感仓库应用 manual approval，并配合权限 deny 与 Hooks。
  - Remote Development 需在**远程主机**装插件，WSL2 下常见检测失败；只有 Bedrock/Vertex/Foundry 凭证时 Web、部分 Code Review、Routines 可能不可用。
- **原文**：https://agent.jeffjade.com/claude-code/platforms-overview/

## 跨章节关联

- **同站上下文**：本册承接 [安装与配置](https://agent.jeffjade.com/claude-code/installation-setup/) 与 [基于第三方 API](https://agent.jeffjade.com/claude-code/third-party-api/)（登录方式决定密钥怎么传、`--model` 填什么）；向前接 [代理循环与工具](https://agent.jeffjade.com/claude-code/agent-loop/) 与 [Plan Mode](https://agent.jeffjade.com/claude-code/plan-mode/)——`/plan` 与 `Shift+Tab` 切的是同一套权限模式，执行段靠 `ExitPlanMode` 而非某个 `/execute`。
- **记忆线**：`/init` 是 [项目记忆总览](https://agent.jeffjade.com/claude-code/claude-md/) 的入口动作，`/memory` 编辑 CLAUDE.md 与自动记忆；跨平台共享 `~/.claude/` 正是记忆能跟着人走的物理原因，团队规范见 [团队记忆落地](https://agent.jeffjade.com/claude-code/memory-team-playbook/)。
- **上下文线**：[上下文管理与多代理](https://agent.jeffjade.com/claude-code/context-management/) 是 `/clear`、`/compact`、`/btw` 三件套的策略延伸，`/rewind` 是它的回退手段；`/loop` 属会话内重复，与云端 [Routines 与定时自动化](https://agent.jeffjade.com/claude-code/routines-automation/) 分属两套调度。
- **扩展线**：`/agents` → [SubAgents](https://agent.jeffjade.com/claude-code/subagents/)；bundled skill 与自建命令 → [Skills](https://agent.jeffjade.com/claude-code/skills/)；`/mcp` → [MCP 协议](https://agent.jeffjade.com/claude-code/mcp/)；`/simplify` 的三路并行审查就是子代理协作的现成样例；`--remote`、`--teleport`、`/remote-control` → [远程会话与 Channels](https://agent.jeffjade.com/claude-code/remote-sessions-channels/)。
- **与通用 Harness 概念的对应**：代理循环 = "意图→规划→读→改→验"闭环；工具 = Read/Edit/Bash/Grep；权限 = `deny → ask → allow` 与四种模式；上下文 = `/context`/`/compact`/`/clear`/`/btw`；记忆 = CLAUDE.md + `~/.claude/`；Skill = bundled skill 与自定义命令共用机制；子代理 = `/agents`、`/batch`、`/simplify`；MCP = `/mcp` 与 `/mcp__<server>__<prompt>`；沙箱 = `/sandbox` 切换沙箱 Bash，以及"`bypassPermissions` 只用于隔离环境"这条底线，详见 [沙箱隔离机制](https://agent.jeffjade.com/claude-code/sandboxing/) 与 [安全边界与权限心智](https://agent.jeffjade.com/claude-code/security-permissions/)。

## 自测（3 题）

1. 同一台机器上，IDE 扩展里 `/resume` 看不到终端里刚结束的会话，最可能的原因是什么？
   提示：先确认两者是否指向同一个 `~/.claude/`（不同用户或不同环境变量启动会分家），再查扩展是否用了自带状态的 CLI 实例；不要先假设会话已丢失。
2. `/compact` 与 `/clear` 各自解决什么问题？为什么任务切换时不该用 `/compact`？
   提示：`/compact` 只压缩同一条任务的对话（可加焦点说明）并保留任务连续性；`/clear` 清空上下文另起话题，旧会话仍可从 `/resume` 找回。跨任务压缩会把无关细节混成一份摘要。
3. 在 CI 里跑 `claude -p` 只得到权限拒绝，为什么？应该在哪一层修？
   提示：`-p` 非交互、没有弹窗授权通道，未命中 allow 的工具会被拒；应在项目 `.claude/settings.json` 的 `permissions.allow` 预置具体命令模式（如 `Bash(npm run test *)`），注意 deny 优先，别用宽泛通配把危险命令一起放进来。
