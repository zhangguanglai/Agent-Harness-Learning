# Claude Code · 第五部分 高级扩展

> 本分册整理自智能体漫游（agent.jeffjade.com），覆盖章节：
> - Hooks：自动化、安全校验与生命周期控制 —— https://agent.jeffjade.com/claude-code/hooks/
> - Skills：构建可重用提示模板与工作流 —— https://agent.jeffjade.com/claude-code/skills/
> - Plugins：通过市场扩展 Claude Code —— https://agent.jeffjade.com/claude-code/plugins/
> - SubAgents：上下文隔离与复杂任务拆解 —— https://agent.jeffjade.com/claude-code/subagents/
> - Agent Teams 与多会话协作 —— https://agent.jeffjade.com/claude-code/agent-teams/
> - MCP：为 Claude Code 接入外部工具与服务 —— https://agent.jeffjade.com/claude-code/mcp/

## 本册要点

- **Hooks 是确定性的最后一层**：`PreToolUse` 在权限 `allow` 规则之前执行，硬拦截必须 `exit 2`（或 JSON 里 `permissionDecision: deny`），`exit 1` 不会阻止工具执行。
- **Skill 的成本模型是懒加载**：正文只在被调用时进入对话并留住，`description` 决定自动触发，`disable-model-invocation: true` 用来管住有副作用的流程。
- **SubAgent 用独立上下文窗口换摘要**：中间工具输出留在子窗口，子代理不能再派子代理；后台子代理不弹权限窗，未预先批准即自动拒绝。
- **Plugin 只是分发单元**：先 `marketplace add` 注册目录，再 `plugin install` 装包；装前必须读 Discover 的 Will install 与 Context cost。
- **MCP 把「你搬运数据」换成「模型自己查询」**：工具暴露为 `mcp__server__tool`，作用域优先级 local > project > user > 插件 > claude.ai 连接器。

## 逐章笔记

### Hooks：自动化、安全校验与生命周期控制

- **一句话**：在代理循环的固定节点（会话 / 回合 / 工具调用）跑你指定的命令，把「模型应该做」升级成「一定发生」，不依赖它是否记得。
- **机制/要点**：
  - 确定性谱系：提示与 CLAUDE.md 低（靠模型自觉），Skills 中，权限 `allow`/`deny` 高（规则匹配工具调用），Hooks 最高（你的脚本必定执行）。
  - 它在循环壳层上插桩：`UserPromptSubmit` → 模型选工具 → `PreToolUse`（可阻断本次调用）→ 权限检查 → 工具执行 → `PostToolUse` / `PostToolUseFailure` → `Stop`。
  - 于是有了关键次序：**Hook 早于 allow 规则**。即使用户本地配置了宽松的 `allow`，`PreToolUse` 仍可 `deny`，这是组织级策略的落脚点。
  - 三类节奏：会话级 `SessionStart`/`SessionEnd`；回合级 `UserPromptSubmit`/`Stop`；工具级 `PreToolUse`/`PostToolUse`。扩展事件有 `Notification`、`PermissionRequest`、`PostToolBatch`、`InstructionsLoaded`、`ConfigChange`。
  - 配置三层嵌套：`hooks.<事件>[]` → `matcher` → `hooks[]{type, command, if}`；matcher 写精确工具名、`Edit|Write`，含其它字符则按 JS 正则匹配（如 `mcp__github__.*`）。
  - 单个 handler 可加 `if`（与权限规则同语法，如 `"Bash(git *)"`、`"Edit(*.ts)"`），避免每条命令都启一次脚本。
  - 反馈契约两种：`exit 2` + stderr 原因 = 硬拒绝；`exit 0` 且 stdout 打 `hookSpecificOutput`（含 `permissionDecision`、`additionalContext`）= 结构化决策，两者不要混用。
  - Handler 类型：`command` 最常用；`http` 把事件 POST 给审计 / SIEM；`mcp_tool` 复用已连 MCP 做校验；`prompt` / `agent` 处理需模型判断的模糊策略，代价是延迟与成本。
  - `async: true` 把日志类 Hook 放后台不阻塞主循环；带 `asyncRewake` 时后台 `exit 2` 可唤醒 Claude。
- **命令 / 配置 / 代码**：

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          { "type": "command",
            "command": "jq -r '.tool_input.file_path' | xargs npx prettier --write" }
        ]
      }
    ]
  }
}
```

- **拦截脚本骨架**：不要只写提示词，让脚本读 `jq -r '.tool_input.command'`，命中危险模式就 `echo "原因" >&2` 后 `exit 2`，否则 `exit 0`；脚本放 `.claude/hooks/` 并 `chmod +x`，配置里用 `${CLAUDE_PROJECT_DIR}/.claude/hooks/block-rm.sh` 引用以便随仓库提交。配置里若已有 `hooks` 对象，只追加事件键，不要整体覆盖。

- **坑与边界**：
  - `PostToolUse` 触发时文件已落盘，**不能撤销**写入，只能再改一次磁盘；要阻止必须在 `PreToolUse`。
  - 用 `exit 1` 做策略拦截无效——对多数事件它只记非阻塞错误，不阻止工具执行。
  - stdout 混入 shell 启动信息（如 `.bashrc` 里的 echo）会导致 `JSON validation failed`；脚本忘记 `chmod +x` 则静默不执行。
  - 团队的 Hook 可能被 `allowManagedHooksOnly` 一类托管策略禁用；`/hooks` 面板只读，增删改请直接编辑 JSON。
- **原文**：https://agent.jeffjade.com/claude-code/hooks/

### Skills：构建可重用提示模板与工作流

- **一句话**：把「某类任务怎么做」写成 `SKILL.md`，正文懒加载、可 `/name` 调用、可把命令输出注入提示，替掉反复粘贴的操作手册。
- **机制/要点**：
  - 与 CLAUDE.md 的差别不在内容而在**生命周期与成本**：CLAUDE.md 会话启动即全文注入；Skill 的 `SKILL.md` 只在被调用后进入对话，长材料拆到 `reference.md` 由模型按需再读。
  - 旧自定义命令已并入 Skills：`.claude/commands/deploy.md` 与 `.claude/skills/deploy/SKILL.md` 等价，同名时 **Skill 优先**；Skill 额外有附属目录、调用方控制、动态上下文、`allowed-tools`。
  - 级别与覆盖：企业 > 个人 `~/.claude/skills/` > 项目 `.claude/skills/` > 插件（命名空间 `plugin-name:skill-name`，避免撞名）。
  - 发现范围：自 cwd 向上到仓库根，加载沿途 `.claude/skills/`；编辑子目录文件时还会按需发现嵌套的 `.claude/skills/`（monorepo 按包拆分）；`--add-dir` 目录里的技能会被加载。
  - 两个独立开关各管一侧：`disable-model-invocation: true` 禁模型自动选（有副作用流程），`user-invocable: false` 只在 `/` 菜单隐藏、仍当背景知识——后者**不**阻止 Skill 工具调用。
  - 动态上下文：行内 `` !`git diff HEAD` `` 与多行 `!` 代码块都在模型看到技能**之前**执行一次，输出以纯文本插入，且不会二次解析新的占位符；企业可设 `disableSkillShellExecution: true` 换成策略提示（捆绑技能与托管技能不受影响）。
  - 参数替换：`$ARGUMENTS`、`$0`/`$1`/`$ARGUMENTS[0]`、命名参数 `$name`（配 `arguments:`）、`${CLAUDE_SESSION_ID}`、`${CLAUDE_SKILL_DIR}`；正文无 `$ARGUMENTS` 而调用带参时，运行时会补一行 `ARGUMENTS: ...`。
  - 生命周期与压缩：渲染后的正文作为**一条消息**留在对话中，运行时不会每轮重读磁盘；自动压缩时每技能保留最近一次调用的前 5000 token、合计约 25000，从最近调用开始填充。
  - 内容分两类：参考型（约定、风格，默认让模型自动加载）与任务型（部署、提交等有副作用流程，配 `disable-model-invocation: true` 主要靠人手 `/name`）。
  - 捆绑技能与自建技能同机制（`/simplify`、`/batch`、`/debug`、`/loop`、`/claude-api`）：本质是交给模型的详细提示，由模型自己编排工具；`/compact`、`/help` 之类才是 CLI 固定逻辑。
- **命令 / 配置 / 代码**：

```markdown
---
description: 总结未提交变更并标出风险。在用户问改了什么、要写 commit message 或 review diff 时使用。
---
## 当前变更
!`git diff HEAD`

## 指令
用两到三条要点总结上方 diff；若发现缺少错误处理、硬编码、测试遗漏等风险，逐条列出。
```

```bash
mkdir -p ~/.claude/skills/summarize-changes   # 目录名即 /summarize-changes
```

- **目录结构**：`SKILL.md`（必填）+ `reference.md` / `examples.md` / `scripts/`（可选）；正文建议约 500 行以内。

- **坑与边界**：
  - `description` 太泛会频繁误触发，太窄则从不自动触发；`description` + `when_to_use` 展示上限约 1536 字符，技能一多会被截断（`/doctor` 查 listing 预算，或把低优先级技能设 `skillOverrides: name-only`）。
  - `context: fork` 只适合正文里有**可执行任务**的技能；纯约定型正文会让子代理很快空返回。
  - `allowed-tools` 在信任工作区后生效，恶意技能可借此扩大工具权限；`~/.claude/skills/` 不要写密钥。
  - 「说着说着就不听了」多半是压缩后正文被淡化，重新 `/skill-name`，或把关键约束改交 Hook 做确定性 enforcement。
- **原文**：https://agent.jeffjade.com/claude-code/skills/

### Plugins：通过市场扩展 Claude Code

- **一句话**：把 skills、hooks、agents、MCP servers、commands 打成可安装包，经 marketplace 一次装齐整套能力。
- **机制/要点**：
  - 两步必须分清：`marketplace add` 只是注册目录（相当于订阅商店，不装任何东西），`plugin install` 才挑具体包；装完 `/reload-plugins` 激活。
  - 组件落地后带命名空间（`plugin-name:skill-name`），与个人 / 项目技能的同名覆盖规则解耦。
  - scope 三档决定归属：User（本机所有项目）、Project（当前仓库、协作共享、入 Git）、Local（仅本机 + 当前仓库）。
  - 装前读 Discover 详情页：**Will install**（将落地的 commands / agents / skills / hooks / MCP / LSP）与 **Context cost**；官方市场 `claude-plugins-official` 一般可在 `/plugin` 里直接浏览。
  - 层次分工：Skill 写流程、Hook 保证必跑、SubAgent 隔离上下文、MCP 连外部系统，Plugin 只负责**打包与分发**，本身不引入新机制。
  - 自建市场的路径：仓库内组织 `plugins/` 与 `plugin.json` → 用 git 仓库或静态 URL 作市场源 → 同事 `marketplace add your-org/your-marketplace` → `plugin install name@marketplace-id` 并选 scope。
  - 反向案例：`gstack` **不是** Plugin，而是角色化斜杠命令集，克隆到 `~/.claude/skills/gstack` 后跑 `./setup`，不能用 `/plugin install`。
  - 值得作为起点的来源：Claude Code Setup（只读分析代码库后按栈推荐 hooks/skills/MCP）、Code intelligence（如 `typescript-lsp`，需本机 language server binary）、Code Review、Context7、Superpowers（`superpowers@claude-plugins-official`）。
- **命令 / 配置 / 代码**：

```text
/plugin                                              # 打开 Discover 浏览
/plugin install github@claude-plugins-official
/plugin marketplace add anthropics/skills
/plugin install document-skills@anthropic-agent-skills
/plugin install example-skills@anthropic-agent-skills
/reload-plugins                                      # 安装后激活
```

```bash
# gstack：目录安装，非 marketplace
git clone --single-branch --depth 1 https://github.com/garrytan/gstack.git ~/.claude/skills/gstack
cd ~/.claude/skills/gstack && ./setup
```

- **坑与边界**：
  - Plugin 在你的用户权限下执行任意代码，`plugin install` / `marketplace add` 等同引入供应链；GitHub star 只作参考，不能代替对 `allowed-tools` 与清单的审查。
  - 装多了会出现 `/` 菜单技能爆炸、描述被截断；用 `/doctor` 查 listing，`skillOverrides` 折叠或直接卸载低优先级包。
  - 多个端到端方法论包并存会重复自动触发，只保留一个；安装前先查重名与流程重叠。
  - LSP 类插件报 `LSP Executable not found` 是本机缺 binary，不是插件损坏；`Plugin not found` 多为市场未添加或过期，需 `marketplace update`。
  - 只有一两个项目专用流程时，直接 `.claude/skills/` 入仓比装包更简单。
- **原文**：https://agent.jeffjade.com/claude-code/plugins/

### SubAgents：上下文隔离与复杂任务拆解

- **一句话**：把会产生大量中间输出的工作放进独立上下文窗口执行，主会话只收一段摘要，避免噪声淹没后续改码。
- **机制/要点**：
  - 委派链路：主模型按各子代理的 `description` 判断是否匹配 → 调用 **`Agent` 工具**（旧配置写作 `Task`，为别名）→ 子代理在独立窗口跑多轮工具循环 → 结束只把摘要写回主会话，中间每步工具默认不逐条展示。
  - 子代理拿到的是**自己的系统提示**（内置定义或你的 `.md` 正文）+ 工作目录等基础信息，**不继承**主系统提示；这也是「换个人格办事」的实现方式。
  - **子代理不能再派子代理**；多层分工由主会话串联多次委派，或从 Skill 侧用 `context: fork` 发起。因此普通定义里写 `Agent(worker,researcher)` 白名单无效，只有 `claude --agent` 把该定义当主线程时才生效。
  - 权限是「继承再裁剪」：`tools` 白名单、`disallowedTools` 黑名单、`permissionMode`；**后台**子代理不弹窗，未预先批准的调用自动拒绝，前台则照常询问；禁用某个子代理写 `permissions.deny: ["Agent(Explore)"]`。
  - 内置子代理：`Explore`（Haiku、只读、带 thoroughness `quick`/`medium`/`very thorough`）、`Plan`（Plan Mode 下只读调研）、`general-purpose`（全套工具）、`claude-code-guide`、`statusline-setup`。
  - 模型解析顺序（简化）：`CLAUDE_CODE_SUBAGENT_MODEL` → 单次调用参数 → frontmatter `model` → 主会话模型。
  - 存储与覆盖：企业托管 > `--agents` JSON（仅当前进程）> 项目 `.claude/agents/` > `~/.claude/agents/` > 插件；`name` 全局唯一，重名会静默丢弃其一；`/agents` 界面改动立即生效，直接改磁盘 `.md` 通常需重启会话。
  - 子代理局部能力：frontmatter `hooks`（仅存活期生效，`Stop` 记为 `SubagentStop`）、`mcpServers`（只挂给子代理，主窗口工具列表不出现，省描述 token）、`skills`（启动时预加载全文）、`memory`、`isolation: worktree`。
  - 四种典型用法：隔离高噪声输出（跑完整测试套件只回失败用例）、并行调研（互不依赖的目录）、串行流水线（先审查再修前 3 条）、worktree 实验（改了不留痕，无变更自动清理）。
- **命令 / 配置 / 代码**：

```markdown
---
name: code-reviewer
description: 代码质量与安全审查。在较大 diff 或 PR 前主动使用；只读，不修改文件。
tools: Read, Grep, Glob, Bash
model: sonnet
---
你是资深代码审查者。被调用时：1) 用 git diff 或用户指定范围看变更；
2) 按「必须修 / 建议修 / 可选优化」分级；3) 每条给出文件位置与改法示例。
```

```bash
claude --agent code-reviewer                   # 整个会话套用该定义
export CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1  # 全局禁用后台子代理
```

- **坑与边界**：
  - 后台模式不弹权限窗，需要逐条批准危险命令时必须改前台重试同一任务；`Ctrl+B` 可把运行中任务挂到后台。
  - 插件自带的 agent 定义会**忽略** `hooks`、`mcpServers`、`permissionMode`，要这些能力得把文件复制到 `.claude/agents/`。
  - `--add-dir` 只扩文件访问，不会从附加目录加载子代理定义；跨项目共享请用个人目录或插件。
  - 每个子代理都返回长篇报告时，主会话仍会被摘要总量撑满——任务描述里要写清目录范围与输出格式。
  - Fork 模式（`CLAUDE_CODE_FORK_SUBAGENT=1`，需 v2.1.117+，实验）继承主会话完整对话与工具，工具调用仍不写入主 transcript，只回最终结果。
- **原文**：https://agent.jeffjade.com/claude-code/subagents/

### Agent Teams 与多会话协作

- **一句话**：多个**独立** Claude Code 会话组成队伍，共享任务列表、队友之间可点对点发消息，由主会话或你统一协调。
- **机制/要点**：
  - 与 SubAgents 的根本差别是**进程模型与通信**：子代理是单会话内的子循环、只能回摘要；Teams 是多会话 + 共享任务板 + 双向消息。
  - 四种并行方式要分清：SubAgents（一个大脑派探子）、agent view（多会话各自独立、只做统一监控）、git worktree（文件隔离、无自动通信）、Agent Teams（可互发消息、可辩论）。
  - 适用三场景：并行 code review（按安全 / 性能 / 测试维度分工后由主会话汇总）、竞争假设调试（两个队友用证据互相反驳）、大模块拆分（前后端各改各目录，靠消息同步接口约定）。
  - 成本结构不同：Teams 是多实例、高 token（页面以 plan 模式队友约 7x 量级举例，并注明以实测为准）；SubAgents 共用一个计费会话外壳、只多子循环，通常更省。
  - 由此得出默认策略：团队先 SubAgents，只有需要会话间辩论或长期并行时才开 Teams。
  - 与上下文管理叠加：主会话 Plan 拆任务 → 队友执行 → 主会话合并结论；单会话仍过长再走 handoff，且别让每个队友都扫全库。
  - 启用步骤、环境变量名、Desktop/Web 支持随版本变化（可能属实验或受 Plan 限制），页面明确要求以官方 agent-teams 文档与 `/doctor` 为准（确切开关：原文未明确）。
- **命令 / 配置 / 代码**：页面只给流程示意，未给固定命令（原文未明确具体 CLI）：

```text
主会话：Plan 拆任务 → 创建 team、添加队友会话、分配共享任务
队友 A（只读）：按维度审查同一 diff，结论写回任务板
队友 B（只写）：按消息中约定的接口实现
主会话：合并结论；若单会话仍过长 → /handoff
```

- **坑与边界**：
  - 任务描述重叠 → 队友结论互相矛盾；应显式划分只读 / 只写边界与各自输出格式。
  - 多个 Opus 队友并行会把账单推高，先从换 Sonnet 队友、降并行度入手。
  - 「消息无人回复」多半是队友会话没启动，用 agent view 看状态。
  - 只想要单窗口隔离却开 Teams 属选型错误，应回到 SubAgent；只缺文件隔离则用 worktree。
- **原文**：https://agent.jeffjade.com/claude-code/agent-teams/

### MCP：为 Claude Code 接入外部工具与服务

- **一句话**：用开放协议把外部系统接成模型可自行调用的工具，替掉「从 JIRA 粘贴需求、从 Sentry 复制报错」的人肉搬运。
- **机制/要点**：
  - 三角色：**Host**（Claude Code，管理多个 Client）→ **Client**（与一个 Server 一对一连连接，把能力注册给 Host）→ **Server**（本机或远程进程，经 JSON-RPC 2.0 暴露能力）。
  - 握手顺序决定能力发现方式：连接时 `initialize` 协商能力，随后 `tools/list` 列出工具；之后模型自己决定调哪个、传什么参数，结果回灌为后续推理的上下文。
  - 三种原语在 Claude Code 的落点：Tools → 模型可见工具，命名 `mcp__serverName__toolName`；Resources → 用 `@server:path` 引用并注入上下文；Prompts → 进 `/` 菜单，形如 `/mcp__serverName__promptName`。
  - 传输三种：stdio（本地 `npx` 起的进程，零网络开销但要管进程生命周期）、HTTP / Streamable HTTP（远程推荐，支持 OAuth）、SSE（**已废弃**，只作向后兼容）。
  - 作用域与优先级：local（默认，写 `~/.claude.json` 按项目路径隔离）> project（项目根 `.mcp.json`，可入 Git 全员共享）> user（本机所有项目）> 插件提供 > claude.ai 连接器；同名只连优先级最高者，故可用 local 覆盖团队配置换 key。
  - 两道安全闸：首次加载团队 `.mcp.json` 会弹批准提示（防恶意仓库借 MCP 拿权限）；密钥用 `${VAR}` / `${VAR:-default}` 外置，团队提交模板、个人各自填。
  - CLI 硬规则：`--transport`、`--scope`、`--env` 必须放在服务器名**之前**，`--` 分隔服务器名与启动命令。
  - 典型接法：PostgreSQL（`@bytebase/dbhub` + 只读 DSN）、Sentry（`--scope project`）、Playwright（浏览器自动化，也可只挂给子代理）；另有 `add-json` 灌现成配置、`add-from-claude-desktop`（仅 macOS / WSL）。
- **命令 / 配置 / 代码**：

```bash
claude mcp add --transport http github https://api.githubcopilot.com/mcp/ \
  --header "Authorization: Bearer YOUR_GITHUB_PAT"      # 远程 HTTP
claude mcp add --transport stdio --env AIRTABLE_API_KEY=YOUR_KEY airtable \
  -- npx -y airtable-mcp-server                        # 本地 stdio
claude mcp add --transport http sentry --scope project https://mcp.sentry.dev/mcp
claude mcp list                        # 列出服务器与状态
claude mcp get <name>                  # 查看单服务器配置
claude mcp remove <name>               # 移除（会话内 /mcp 看状态与认证）
```

```json
{ "mcpServers": { "api-server": {
  "type": "http",
  "url": "${API_BASE_URL:-https://api.example.com}/mcp",
  "headers": { "Authorization": "Bearer ${API_KEY}" } } } }
```

- **坑与边界**：
  - 选项位置写错（放到服务器名之后）或漏掉 `--`，stdio 服务器会被当成参数而非启动命令。
  - 工具描述本身吃上下文：装太多 Server 需靠工具搜索类机制压占用（本章该节细节原文未完整抓取）。
  - 企业侧有两道闸：`managed-mcp.json` 独占控制、allowlist / denylist 策略（具体键名与格式：原文未明确）。
  - SSE 传输已废弃，新接入一律走 HTTP；只接一家 SaaS 时单独 `claude mcp add` 比整包 Plugin 更透明。
- **原文**：https://agent.jeffjade.com/claude-code/mcp/

## 跨章节关联

- **第三部分《代理循环与工具》**：Hooks 是循环壳层上的插桩点，`PreToolUse` 卡在「模型选完工具」与「权限检查」之间——理解了这一点才能解释它为何能压过用户的 `allow`。SubAgent 与 MCP 分别是循环的横向（换上下文窗口）与纵向（扩工具面）延伸。
- **第四部分《项目记忆总览》《CLAUDE.md 编写与维护》**：CLAUDE.md 存「项目是什么、默认怎么做」，应压到 200 行内的高频事实；一旦写成操作手册就迁到 Skill（懒加载），写成「必须发生」就迁到 Hook。记忆 / 流程 / 强制是三层，不能互相顶替。
- **第三部分《Plan Mode》**：Plan Mode 解决「先想再做」并派生只读 Plan 子代理；Hooks 解决「到这一步必须发生什么」；Skill 的 `context: fork` 让评审类流程也能在隔离窗口里跑。
- **第七部分《上下文管理与多代理》《Agent SDK 程序化调用》**：SubAgent 是事前的隔离，`/compact` 与 handoff 是事后的补救；Agent SDK 把同一套委派与工具模型搬到程序里。
- **第九部分《沙箱隔离机制》《安全边界与权限心智》《团队与组织级落地》**：`permissions.deny` 是可读的策略面，Hook 的 `exit 2` 是可编程兜底，MCP 的批准提示与 Plugin 的供应链审查同属「谁能以什么身份执行代码」；`isolation: worktree` 是文件层的隔离。
- **通用 Harness 概念映射**：代理循环 ↔ `PreToolUse`/`PostToolUse`/`Stop` 插桩点；工具 ↔ `mcp__*` 工具与 `allowed-tools` 白名单；权限 ↔ `permissionMode` / `permissions.deny` / `Agent(name)`；上下文 ↔ Skill 懒加载与子代理独立窗口；记忆 ↔ CLAUDE.md、`memory` 目录、auto memory；Skill ↔ `SKILL.md` + `context: fork`；子代理 ↔ `.claude/agents/*.md`；MCP ↔ Host/Client/Server；沙箱 ↔ worktree 隔离与后台不弹窗的自动拒绝。

## 自测（3 题）

1. **`PreToolUse` 与 `PostToolUse` 各能拦住什么？为什么硬拦截要用 `exit 2` 而不是 `exit 1`？**
   提示：前者在工具执行前可阻断，后者触发时文件已落盘、只能再改一次；`exit 1` 对多数事件只记非阻塞错误，不阻止调用。
2. **「部署到生产」这类流程，该让模型自动选用还是加 `disable-model-invocation: true`？其中「必须先跑测试」的约束又该放哪？**
   提示：有副作用 → 禁自动调用、只留 `/deploy`；「必须执行」的步骤放 Hook，别只写在 SKILL.md 正文里指望模型记得。
3. **要全库搜鉴权实现并读几十份日志，用 SubAgent 还是 Agent Teams？若两个会话需要互相反驳结论呢？**
   提示：单会话高噪声探索用 SubAgent（只回摘要、成本低）；需要会话间双向通信与共享任务板才用 Teams，并先划清只读 / 只写边界。
