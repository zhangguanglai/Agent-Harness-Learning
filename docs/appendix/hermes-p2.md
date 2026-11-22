# Hermes Agent · 第二部分 核心机制

> 本分册整理自智能体漫游（agent.jeffjade.com），覆盖章节：
> - 记忆、学习与 Skill — https://agent.jeffjade.com/hermes-agent/memory-learning-skills/
> - 配置与个性化 — https://agent.jeffjade.com/hermes-agent/configuration-personalization/
> - 工具系统 — https://agent.jeffjade.com/hermes-agent/tools-system/

## 本册要点

- 「记住」被拆成三条腿：`MEMORY.md`/`USER.md`（声明式、每次会话冻结注入）、Skill（程序性、渐进披露）、`session_search`（FTS 检索历史原文），三者互补且不可互相顶替。
- 会话中途 `memory add` 只落盘、不刷新当前系统提示块；要求「当场生效」的约束必须写进用户消息、`AGENTS.md` 或 `SOUL.md`。
- 配置优先级为 CLI 参数 > `config.yaml` > `.env` > 内置默认，密钥只进 `.env`，`hermes config set` 会自动分流两处。
- Profile 隔离的只是 `HERMES_HOME` 状态目录，不是文件系统沙箱；不可信执行必须把 `terminal.backend` 切到 `docker`/`ssh`/云沙箱。
- 破坏性命令有三层防线：审批模式（`manual`/`smart`/`off`）、会话级 YOLO、hardline blocklist（YOLO 也绕不过）。

## 逐章笔记

### 记忆、学习与 Skill

- **一句话**：Hermes 用「精炼事实常驻 + 流程按需加载 + 历史按需检索」三件套构成闭环学习，再用 Curator 给自动膨胀的 Skill 库做后台体检。
- **机制/要点**：
  - **三种记忆各司其职**：`MEMORY.md` 存环境/惯例/踩坑，`USER.md` 存身份与沟通偏好（都由 `memory` 工具维护，没有 `read` action，因为内容已在系统提示里）；Skill 存多步可复用流程；`session_search` 查 `~/.hermes/state.db` 里带 FTS5 索引的原始会话，返回真实消息而非 LLM 摘要。
  - **frozen snapshot 是为 Prompt Caching 交的税**：会话启动时把记忆文件渲染成系统提示里的固定块并在整轮会话中冻结；工具写入立刻改磁盘，但当前提示不重建，改由工具返回值给出实时条目状态做补偿。动机是让提示前缀逐字节稳定，从而命中缓存。
  - **`replace`/`remove` 用 `old_text` 做唯一子串定位**，不必粘贴整段；子串命中多处会报错并要求更具体的锚点——用可读性换掉误伤无关条目的风险。典型调用：`memory(action="replace", target="memory", old_text="dark mode", content="...")`。
  - **字符上限逼出合并策略**：`~/.hermes/config.yaml` 的 `memory_char_limit: 2200`（约 800 tokens）与 `user_char_limit: 1375`（约 500 tokens）是硬上限，另有 `memory_enabled`、`user_profile_enabled` 两个开关；写满后 `add` 直接失败；占用超过约 80% 就应主动 `replace` 合并，而不是删历史。
  - **Skill 三级渐进披露**：系统提示只放 Level 0（`skills_list` 的名称+描述，约 3k tokens 量级），需要步骤才 `skill_view(name)` 取全文，再要细节才 `skill_view(name, path)` 读 `references/` 单文件。目录默认 `~/.hermes/skills/`，兼容 agentskills.io，每个 Skill 同时是一个 Slash。
  - **Curator 不是 cron 守护进程**：仅在 CLI 启动或 Gateway 空闲 tick 时判定「距上次 ≥ `interval_hours`(168) 且空闲 ≥ `min_idle_hours`(2)」，满足才 fork 独立 `AIAgent` 做两阶段——确定性淘汰（30 天未用标 `stale`、90 天进 `.archive/`）+ 辅助模型审查（提议合并/patch/归档）。它只跳过 `.bundled_manifest` 与 `.hub/lock.json` 两类，**无法区分人手写与 Agent 写的**。
- **命令 / 配置 / 代码**：

  ```bash
  hermes curator run --dry-run      # 预览将被 stale / 归档的 Skill，不改动
  hermes curator pin my-workflow    # 人手写的重要 Skill 先钉住
  hermes curator restore my-workflow
  hermes sessions list              # 浏览会话库，配合 /resume 打开完整会话
  ```

  ```markdown
  ---
  name: my-skill
  description: 一句话说明用途
  ---
  ## When to Use    # 触发条件
  ## Procedure      # 步骤
  ## Pitfalls       # 已知失败模式
  ## Verification   # 如何确认成功
  ```

- **坑与边界**：
  - 说「已记住」但同一会话行为不变，多半是 frozen snapshot 未在下一轮会话生效；处理是 `/new` 或重开会话，绝不要反复 `add` 堆重复条目。
  - Curator 不理解业务关键性，自己手写的 Skill 必须事先 `pin`，否则可能被标 `stale` 或归档；每次真实运行前会备份到 `.curator_backups/`，可用 `hermes curator rollback` 回滚。
  - `hermes skills install` 只能由用户执行，Agent 不能自行从未信任源拉 Hub 包；`skills.external_dirs` 只读扫描，写入永远落在 `~/.hermes/skills/`。
  - 不要把长篇教程塞进 memory：那应写成 Skill 或项目内文档，memory 只留索引级事实；也不要用 `session_search` 替代常驻事实（有延迟且占用对话轮次）。
- **原文**：https://agent.jeffjade.com/hermes-agent/memory-learning-skills/

### 配置与个性化

- **一句话**：把「密钥 / 设置 / 人格 / 项目规则 / 多套身份」分别落到 `.env`、`config.yaml`、`SOUL.md`、`AGENTS.md` 与 Profile，并说清各自何时进上下文。
- **机制/要点**：
  - **目录即分层**：`config.yaml` 放非密钥设置，`.env` 放 Key/token/SSH/`SUDO_PASSWORD`，`auth.json` 放 OAuth，`SOUL.md` 是全局人格，`memories/`、`skills/`、`sessions/`、`cron/`、`logs/` 分别是记忆、技能、会话、定时任务与脱敏日志。
  - **优先级四层**：CLI 参数 > `config.yaml` > `.env` > 内置默认；`hermes config set` 按内容自动路由（Key 进 `.env`，其余进 `config.yaml`），`${VAR_NAME}` 未定义时原样保留占位符。三种分流别混用：`provider_routing` 在同一次请求内按子任务换模型（成本优化）、`fallback_providers` 在请求失败后按链逃生（可用性）、`credential_pools` 是同 Provider 多 Key 轮换（限流逃生）。
  - **`SOUL.md` 是系统提示的 slot #1**：从 `HERMES_HOME/SOUL.md` 读取，**不从工作目录自动发现**；首次安装缺失会种子一份默认文件但绝不覆盖已有文件；经注入扫描与长度截断后原文注入，无额外包装；空文件或读失败回退内置身份。
  - **`/personality` 是会话级叠加而非落盘**：磁盘上的 `SOUL.md` 是持久基线，临时切换教学模式/脑暴用 `/personality <name>`，自定义名在 `config.yaml` 的 `agent.personalities` 下定义。
  - **项目上下文每种类型只加载一份**：`.hermes.md`/`HERMES.md` → `AGENTS.md` → `CLAUDE.md` → `.cursorrules`，先匹配者胜。启动时 CWD 文件约 20,000 字符上限、头尾截断；子目录 `AGENTS.md` 在工具访问该路径时才渐进注入（约 8,000 字符/文件）——这样提示前缀稳定，利于缓存。
  - **Profile 是独立的 `HERMES_HOME`**：各有 config、`.env`、`SOUL.md`、sessions、skills 与 Gateway 进程，额外 profile 落在 `~/.hermes/profiles/<name>/`；两个 profile 误用同一 bot token 时，后启动的 Gateway 会被明确拒绝。
- **命令 / 配置 / 代码**：

  ```yaml
  # 该 profile 固定在某项目下开工，避免 cwd 歧义
  terminal:
    backend: local
    cwd: /absolute/path/to/my-project
  ```

  ```bash
  hermes config            # 查看当前配置
  hermes config check      # 升级后检查缺失项；必要时 config migrate
  hermes profile create work --clone        # 复制 config/.env/SOUL.md
  hermes profile create backup --clone-all  # 含记忆与会话的完整快照
  hermes profile use work                   # 粘性默认；单次用 hermes -p coder chat
  hermes profile export coder -o coder-backup.tar.gz
  ```

- **坑与边界**：
  - Profile **不是文件系统沙箱**：`local` backend 下 Agent 仍拥有你用户账号的权限，隔离的只是 Hermes 状态目录；不可信执行要换容器 backend，不能用「多开 profile」代替 Docker/SSH。
  - `cwd: "."` 在 `local` 上表示**启动 Hermes 时所在目录**，不是 profile 目录；CLI 与 Gateway 的默认 cwd 不同，多项目场景应写绝对路径。
  - 改完 `SOUL.md` 语气没变通常是旧会话没重建；`AGENTS.md` 显示 `[BLOCKED: ...]` 说明触发注入扫描被整块拒绝，需删可疑句式——但扫描不能替代对共享仓库的人工审阅。
  - 别把 `approvals.mode` 设 `off` 当开发默认，那等于长期 YOLO；`profile delete` 会连带删该 profile 的全部会话与记忆，先 `export` 再删。
- **原文**：https://agent.jeffjade.com/hermes-agent/configuration-personalization/

### 工具系统

- **一句话**：模型通过 function calling 调工具，Hermes 用 toolsets 控制暴露面、用 terminal backend 决定命令真正跑在哪、用审批与 hardline 叠层兜底，并用 MCP 外接生态。
- **机制/要点**：
  - **在 Agent Loop 中的位置**：模型返回 tool calls → 运行时执行对应 handler → 结果回灌对话 → 再次请求模型，直到产出最终文本；内置工具在 `tools/registry.py` 注册，toolsets 决定当前平台暴露哪一批，避免一次塞入过大的 schema。
  - **toolsets 的三重动机**：缩小 schema 让模型更易选对工具、按平台分策略（Telegram 关掉重型 `browser`，开发机可全开）、每个已连接且有过滤后工具的 MCP 服务器自动对应 `mcp-<server>` 动态集。关掉 `memory`/`skills` 不等于更安全，只是让 Agent 失去闭环学习。
  - **backend 决定隔离边界**：`local` 等同你本人账号权限；`docker` 是单容器持久沙箱（cap-drop 等加固，默认推荐）；`ssh` 适合远程算力且能防 Agent 改自身代码；`modal`/`daytona`/`vercel_sandbox` 是云 VM 型弹性算力；`singularity` 面向 HPC 集群。
  - **Docker 是一个长生命周期容器**：首次需要时启动**一个**容器，后续 `terminal`、`read_file`、`execute_code` 都 `docker exec` 进同一实例；`/new` 与 `delegate_task` 子 Agent 默认共享它，已装包与工作目录延续，进程退出即销毁。`container_persistent: true` 只保留 `/workspace` 一类目录，**不保证**后台进程与 PID 空间延续，并行 delegate 会争用同一 cwd。
  - **三层防护的分工**：审批层（`manual` 人工确认 / `smart` 辅助模型分级 / `off` 全局 YOLO）+ 会话级 `--yolo`、`/yolo`、`HERMES_YOLO_MODE=1` + hardline blocklist（`rm -rf /`、fork bomb、`dd` 写块设备等在审批之前直接拒绝，YOLO 绕不过）。CLI 审批选项 `once|session|always|deny`（默认 deny，`always` 落进 `command_allowlist`），Gateway 侧超时 fail-closed 默认拒绝；容器类 backend 下**容器内**危险模式检查会跳过，因为隔离边界被视为已满足。后台进程用 `terminal(background=true)` 拿到 `session_id` 后，以 `process(action="poll"|"log"|"kill")` 轮询、取日志或杀掉。
  - **MCP 的过滤与安全**：工具注册名形如 `mcp_<server>_<tool>`（如 `mcp_filesystem_read_file`）；stdio 子进程**不继承**完整 shell 环境，只拿到配置里的 `env` 与安全基线；`include`/`exclude` 同时存在时 include 优先；改完配置需 `/reload-mcp`，服务器也可推 `tools/list_changed` 自动刷新；`supports_parallel_tool_calls` 只对确认无共享状态的只读工具开启。
- **命令 / 配置 / 代码**：

  ```yaml
  terminal:
    backend: docker        # local | docker | ssh | modal | daytona | vercel_sandbox | singularity
    timeout: 180           # 命令超时；审批另配 approvals.mode / approvals.timeout
  ```

  ```yaml
  mcp_servers:
    github:
      command: "npx"
      args: ["-y", "@modelcontextprotocol/server-github"]
      env: { GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_TOKEN}" }
      tools: { include: [list_issues, create_issue] }
  ```

  ```bash
  hermes tools                                # 交互式按平台勾选 toolsets
  hermes chat --toolsets "web,terminal,file"  # 本次会话限定
  hermes config set terminal.backend docker
  ```

- **坑与边界**：
  - 装了 Docker 不等于 backend 是 docker，必须显式 `hermes config set terminal.backend docker`；否则你以为在容器里跑的命令实际落在主机 shell 上。
  - 别把 `approvals.mode: off` 当开发默认，一次误判即可删库；`sudo` 卡住 Gateway 是无人输入密码，需评估风险后在 `.env` 设 `SUDO_PASSWORD`。
  - 对不可信 MCP 服务器不要「全工具 + parallel」齐开，用 `include` 做最小暴露，敏感服务可 `sampling.enabled: false`；Telegram 上不要默认开 `terminal` 且无 allowlist。
  - YOLO 仍被拒说明踩到 hardline blocklist，这是设计行为，应在容器外人工执行；同时别用「工具数量」代替任务分解，toolset 过大反而增加误选与 token 消耗。
- **原文**：https://agent.jeffjade.com/hermes-agent/tools-system/

## 跨章节关联

- **册内互指**：记忆章的「mid-session 记忆不即时生效」直接支撑配置章的分工结论——当场要生效的约束写用户消息或 `AGENTS.md`，长期事实交 `memory` 工具；工具章的 `memory`/`skills` toolset 是记忆章那套机制的执行入口，关掉即断掉闭环。
- **与本站其他章节**：Curator 的 `pin`/`restore` 流程在《技能系统实战》展开；外部记忆 Provider 应以独立插件发布（见《插件系统（Plugins）》，不进核心 `plugins/memory/`）；审批、hardline 与容器加固的完整清单在《安全、性能与最佳实践》；toolsets 与 handler 的代码级剖析在《架构拆解》；Gateway 的 `*_ALLOWED_USERS` 授权与 Cron 推送在《消息网关》；`/goal` 自动续跑见《第一次对话》。
- **对应通用 Harness 概念**：代理循环（tool call → handler → 结果回灌）、工具（toolsets / registry / schema 预算）、权限（`approvals.manual|smart|off` + YOLO + hardline + fail-closed 超时）、上下文（frozen snapshot、prompt caching、`AGENTS.md` 渐进注入与 20k/8k 截断）、记忆（声明式 / 程序性 / 检索三条腿）、Skill（Level 0/1/2 渐进披露 + Curator 维护）、子代理（`delegate_task` 共享容器与 cwd 竞争）、MCP（stdio/HTTP 服务器、`include` 过滤、Hermes 反作 MCP server）、沙箱（`local`/`docker`/`ssh`/云 backend 与容器内审批跳过）。

## 自测（3 题）

1. 你在会话里让 Agent「记住用 pnpm 而不是 npm」，它口头确认了，但下一轮仍给出 `npm install`。请解释成因并给出两条修复路径。
   - 提示：frozen snapshot 在会话启动时就冻结了系统提示块，写盘不等于进上下文；修复是开新会话/`/new`，或把约束当场写进当前用户消息与 `AGENTS.md`。
2. `SOUL.md`、`AGENTS.md`、`MEMORY.md` 三者的维护者、注入时机与适用内容分别是什么？为什么四类项目上下文文件只加载一份？
   - 提示：手写静态规范 vs Agent 用 `memory` 工具维护的事实；项目文件按 `.hermes.md`/`HERMES.md` → `AGENTS.md` → `CLAUDE.md` → `.cursorrules` 取首个匹配，`SOUL.md` 独立并行加载；只取一份是为提示前缀稳定、命中缓存并避免规则打架。
3. 你已装了 Docker 也在容器里跑过命令，但一次 `rm -r` 仍直接作用于宿主机目录；同时又发现 YOLO 下某条命令被硬拒。分别怎么解释？
   - 提示：`terminal.backend` 未设为 `docker` 时所有工具走 `local`；hardline blocklist 在审批层之前生效，YOLO 不绕过（`rm -rf /`、fork bomb、`dd` 写块设备等）。
