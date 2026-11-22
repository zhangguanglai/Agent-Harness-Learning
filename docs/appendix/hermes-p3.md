# Hermes Agent · 第三部分 实战

> 本分册整理自智能体漫游（agent.jeffjade.com），覆盖章节：
> - 消息网关 — https://agent.jeffjade.com/hermes-agent/messaging-gateway/
> - 技能系统实战 — https://agent.jeffjade.com/hermes-agent/skills-in-practice/
> - 事件钩子（Event Hooks） — https://agent.jeffjade.com/hermes-agent/event-hooks/
> - Kanban 多 Agent 看板 — https://agent.jeffjade.com/hermes-agent/kanban-multi-agent-board/
> - 高级特性 — https://agent.jeffjade.com/hermes-agent/advanced-features/

## 本册要点

- Gateway 不是第二个产品：它复用 CLI 的 `COMMAND_REGISTRY`、toolsets 与 `~/.hermes/state.db`，只替换入站适配器与出站渲染，并把 Cron 调度搬进常驻进程（约 60s tick）。
- 带 `terminal` 的 bot 默认拒绝陌生人：`*_ALLOWED_USERS` 决定「能否接触」，`allow_admin_from` + `user_allowed_commands` 决定「进来后能跑哪些 Slash」，两层不可互相替代。
- Skill 单一事实来源是 `~/.hermes/skills/`（`external_dirs` 只读）；关键流程必须 `hermes curator pin`，否则约 90 天未用会被移入 `.archive/`。
- 三套钩子不可互换：Gateway 钩子只观测（仅 Gateway 生效），能阻断工具的只有 Plugin 与 Shell 的 `pre_tool_call`，且 Python 插件先于 Shell 脚本生效。
- 并行改码用 Kanban 而非 `delegate_task`：前者是可 block/unblock、跨重启留痕的持久状态机，后者是阻塞式 fork→join；两者绝不能同时跑两套 dispatcher 抢同一 `kanban.db`。

## 逐章笔记

### 消息网关

- **一句话**：把本机 Agent 常驻到 Telegram/Discord 等平台，用「按 chat 分会话 + 默认拒绝陌生人 + 进程内 Cron」变成可团队共用、可手机触达的服务。
- **机制/要点**：
  - **复用而非分叉**：与 CLI 共享 `COMMAND_REGISTRY`、toolsets、`state.db`，差异只在入站平台适配器与出站渲染（流式编辑、附件、语音转写），所以 Slash、审批、Skill 多端行为一致；平台能力差异大（SMS 最简，Telegram/Discord/Slack/Matrix/Feishu 支持流式与线程）。
  - **一条流水线**：适配器 → 按 chat 的 Session store → `AIAgent`（`run_agent.py`）→ 回投；Cron 调度器作旁路每约 60s 扫到期 job，在隔离会话跑完再投递，因此定时任务不依赖纯 CLI 前台进程存活。
  - **fail-closed 的两层授权**：不在 allowlist 且未 pairing 一律拒绝——各平台预设 toolset（如 `hermes-telegram`）默认含 `terminal`，放行等于给一台能跑 shell 的机器；`GATEWAY_ALLOW_ALL_USERS=true` 官方明确不推荐。
  - **接入层与命令面分离**：allowlist/pairing 解决「能否进」，`allow_admin_from` + `user_allowed_commands` 解决「进来后用哪些 Slash」。注意某 scope 未设 `allow_admin_from` 时该 scope **整体不分层**，所有被允许用户拿到完整权限——为兼容旧安装留的坑。
  - **打断默认、熔断不自动恢复**：新消息 SIGTERM 在跑的终端命令（约 1s 后 SIGKILL）、取消在途 tool call、跳过队列、把打断期多条消息合并成一条 prompt（`/stop` 只打断不接新任务）；适配器连续可重试失败会自动暂停并尝试通知其他平台 home，但必须人工 `/platform resume`，避免对已挂平台重试风暴。
- **命令 / 配置 / 代码**：

  ```bash
  hermes gateway setup                      # 交互选平台
  hermes gateway run                        # 前台试跑，终端需保持
  hermes gateway install && hermes gateway start && hermes gateway status
  sudo hermes gateway install --system      # 无图形界面的 VPS
  hermes pairing approve telegram XKGH5N7P  # 配对码约 1 小时过期
  hermes cron create "every 2h" "检查服务器状态"
  hermes dashboard                          # 默认 127.0.0.1:9119
  ```

  ```yaml
  gateway:
    platforms:
      discord:
        extra:
          allow_from: ["111", "222"]
          allow_admin_from: ["111"]              # 不设则此 scope 不分层
          user_allowed_commands: [status, model]
  display: { busy_input_mode: queue, tool_progress: all }   # interrupt | queue | steer
  ```

  会话重置写在 `~/.hermes/gateway.json` 的 `reset_by_platform.<平台>.{mode: idle, idle_minutes}`。Cron 投递 token：`origin`、`telegram`、`telegram:123456`、`all`、`origin,all`；`cron.wrap_response: false` 发裸内容，回复以 `[SILENT]` 开头可抑制成功投递。

- **坑与边界**：
  - 同一机器同时启用 user 与 system 两套 unit 会产生行为歧义（Hermes 会告警）；多 `HERMES_HOME` 时服务名带 hash 后缀，别对错名 restart。
  - Cron 会话内不能再建 cron（防调度循环）；job 默认继承创建时 Gateway 的 Profile、不加载仓库 `AGENTS.md`，要在项目根执行必须 `--workdir`。
  - `/restart`、`/platforms` 是网关侧命令，CLI 里没有；`/background` fork 的 Agent 结果只回到发起命令的那个聊天。
- **原文**：https://agent.jeffjade.com/hermes-agent/messaging-gateway/

### 技能系统实战

- **一句话**：把「写合格 `SKILL.md` → Hub 安装 → Bundle 组合 → Curator 淘汰」串成可运维生命周期，核心矛盾是让 Level 0 索引保持短小又不丢关键流程。
- **机制/要点**：
  - **三层动作别混**：发现（人 `hermes skills browse`，Agent `skills_list`）、加载（`/skill-name`、`skill_view`、`skill_view(name, path)` 读 `references/`）、演进（`install`/`pin`/`curator` 与人，`skill_manage` 与 Agent）。
  - **frontmatter 是契约**：`name` 决定 Slash 名，`description` 就是 Level 0 那一行（写不清 Agent 就瞎猜），`platforms` 在不匹配 OS 上隐藏 Skill，`requires_toolsets` 缺则隐藏、`fallback_for_toolsets` 仅在该 toolset 不可用时兜底，`required_environment_variables` 让 **CLI** 安全提示填密钥——网关聊天永不索要秘密。正文四段：When to Use / Procedure / Pitfalls / Verification。
  - **渐进披露靠目录**：`scripts/` 由 `execute_code` 或 `terminal` 调用且声明的 env 会透传；Hub 多文件 Skill 整目录拉取，但**单 URL 安装只支持单文件 `SKILL.md`**。
  - **供应链控制是设计**：`install` 与 Hub 拉取只能人执行，Agent 只能在本地建；信任级别 `builtin` / `official`（`optional-skills/`）/ `trusted`（白名单仓库）/ `community`（skills.sh、well-known URL、自定义 GitHub），`--force` **不能**绕过 `dangerous` 结论。Bundle 固化组合：同名冲突时 Bundle 优先，缺失成员跳过并提示、不自动安装。
  - **Curator 只看时间不看出身**：距上次 ≥ 7 天且空闲 ≥ 2 小时才 fork 维护 Agent；30 天标 `stale`、90 天进 `.archive/`；跳过 `.bundled_manifest` 与 `.hub/lock.json`，但**分不清人手写与 Agent 写**，故生产流程必须 pin。
- **命令 / 配置 / 代码**：

  ```markdown
  ---
  name: deploy-runbook
  description: 生产发布检查清单与回滚步骤
  platforms: [linux]
  metadata:
    hermes: { tags: [deploy], category: devops, requires_toolsets: [terminal] }
  required_environment_variables:
    - { name: DEPLOY_WEBHOOK, prompt: 部署 webhook URL, required_for: full functionality }
  ---
  ```

  ```bash
  hermes skills inspect openai/skills/k8s && hermes skills install official/security/1password
  hermes skills install https://example.com/SKILL.md --name my-skill   # 仅单文件
  hermes skills check && hermes skills update && hermes skills uninstall k8s
  hermes bundles create backend-dev --skill github-code-review --skill test-driven-development
  hermes curator run --dry-run && hermes curator pin deploy-runbook
  hermes skills reset google-workspace --restore   # 还原被改过的 bundled 副本
  ```

  目录：`~/.hermes/skills/<category>/<name>/{SKILL.md, references/, scripts/, templates/}`，旁挂 `.hub/{lock.json, audit.log}`、`.bundled_manifest`、`.archive/`。回复里写**绝对路径**媒体会被 Gateway 抽成附件，`[[as_document]]` 强制文件发送（避免 Telegram 压图），`[[audio_as_voice]]` 发语音气泡，指令从可见文本剥离。

- **坑与边界**：
  - 只装不治理：Level 0 索引膨胀后模型更容易乱选乱猜，索引长度是质量问题不是存储问题。
  - Agent 自建 Skill 的官方阈值是「约 **5 次以上** tool call 的复杂任务」，社区流传的「15 次」是非官方口径，别当配置依据。
  - 未认证 GitHub API 约 60 次/小时，批量 `check`/`update` 会撞限流，需在 `.env` 设 `GITHUB_TOKEN`（约 5000 次/小时）。
  - 改过 bundled 副本会被标 `user-modified`，此后 `hermes update` 不再覆盖；分工上单行事实进 memory、多步流程进 Skill、仓库规范进 `AGENTS.md`。
- **原文**：https://agent.jeffjade.com/hermes-agent/skills-in-practice/

### 事件钩子（Event Hooks）

- **一句话**：三套钩子按「运行范围 + 能否阻断」分工——Gateway 钩子做观测与外部通知，Plugin/Shell 钩子才是能改写或否决工具调用的策略层，hardline 与审批始终是内置地板。
- **机制/要点**：
  - **同一主循环上的插槽**：`pre_gateway_dispatch`（仅 Gateway）→ Agent Loop → `pre_llm_call`（可注入）→ 工具循环 → `pre_tool_call`（可 block）→ 执行（含审批）→ `post_tool_call`/`transform_tool_result` → `transform_llm_output` → `agent:*`。插错层等于功能失效，例如把阻断写进 Gateway 钩子。
  - **Gateway 钩子不阻塞**：`~/.hermes/hooks/<name>/` 下 `HOOK.yaml` + `handler.py`，函数名必须为 `handle`（支持 `async def`），在 `gateway:startup`、`session:start/end/reset`、`agent:start/end/step`、`command:*` 触发；`command:*` 一次订阅所有 Slash。它没有 `pre_tool_call` 语义，只能记录转发。
  - **注入位置决定缓存友好度**：`pre_llm_call` 返回 `{"context": "..."}` 被追加到**当前轮用户消息**而非 system，代价是仅本轮可见，收益是 system 前缀逐字节不变、Prompt Cache 仍命中，且不写入 SessionDB 的原始消息。
  - **返回值语义严格、顺序即优先级**：`pre_tool_call` 返回 `{"action": "block", "message": "..."}` 让模型看到错误而非静默失败，`transform_tool_result` 返回非空 `str` 即替换，`post_*`/`on_session_*` 返回值被忽略；多个 block 时**先注册者优先**（Python 插件先于 Shell 钩子）。
  - **Shell 钩子门槛最低**：`config.yaml` 的 `hooks:` 声明 matcher 与脚本，Hermes 往 stdin 写 JSON、从 stdout 读 JSON，脚本可用任意语言；首次运行某 `(event, command)` 对会提示确认，除非 `hooks_auto_accept: true`（等于自动执行任意脚本）。
- **命令 / 配置 / 代码**：

  ```yaml
  # ~/.hermes/hooks/my-hook/HOOK.yaml
  name: my-hook
  events: [agent:start, agent:end, agent:step]
  ```

  ```python
  # handler.py —— 函数名必须是 handle；插件侧用 ctx.register_hook("pre_tool_call", fn)
  async def handle(event_type: str, context: dict):
      log = Path.home() / ".hermes" / "hooks" / "my-hook" / "activity.log"
      with open(log, "a") as f:
          f.write(json.dumps({"event": event_type, **context}) + "\n")
  ```

  ```yaml
  hooks:
    pre_tool_call:
      - { matcher: "terminal", command: "~/.hermes/agent-hooks/block-dangerous.sh", timeout: 10 }
    post_tool_call:
      - { matcher: "write_file|patch", command: "~/.hermes/agent-hooks/auto-format.sh" }
  hooks_auto_accept: false
  ```

- **坑与边界**：
  - 在纯 CLI 会话里测 Gateway 钩子必然不触发，不是 bug；要全环境生效只能用 Plugin 或 Shell 钩子。
  - 注入无效先查返回值：`None`、空串、空 context 都被当成「没意见」；也不要在 `pre_llm_call` 里塞整份仓库，用 `@file` 或 `session_search`。
  - 别与 hardline 重复造轮子（`rm -rf /` 已被内置拒绝）；业务规则写进 `SOUL.md` 只是建议，强制只能靠钩子或审批。
- **原文**：https://agent.jeffjade.com/hermes-agent/event-hooks/

### Kanban 多 Agent 看板

- **一句话**：把工作分派从一次函数调用升级为持久工作队列，每步 handoff 是 `kanban.db` 里的一行，因此可跨重启、可人工介入、可事后审计。
- **机制/要点**：
  - **RPC 与队列的本质差别**：`delegate_task` 是 fork→join，父会话阻塞等摘要、失败即失败、无介入点；Kanban create 后 fire-and-forget，工人是具名 Profile（独立记忆），可 block→unblock→重派。工人内部仍可再调 `delegate_task`。
  - **双入口单事实源**：工人调 `kanban_show`/`kanban_list`/`kanban_complete`/`kanban_block` 工具，人与脚本用 `hermes kanban`、`/kanban`、Dashboard，两侧都过 `kanban_db` 层故视图一致；工人 spawn 后首动作是 `kanban_show()`，不会 shell 出 CLI。
  - **状态机就是列**：`Triage → Todo → Ready → In Progress → Blocked → Done`。`Ready` 表示待 dispatcher 领取，工人崩溃可 reclaim；`Blocked` 写 comment 后 unblock 回 `Ready`，这就是「可恢复」的实现。
  - **dispatcher 只能有一个**：默认随 Gateway 内嵌（`dispatch_in_gateway: true`，约 60s tick）；再跑独立 `hermes kanban daemon` 会让两个调度器抢同一 `kanban.db`，表现为重复执行。
  - **workspace 是安全边界**：`scratch` 一次性临时目录；`dir:/abs/path` 共享目录但**必须绝对路径**（`dir:../foo` 这类相对路径被拒，防 confused-deputy）；`worktree` 用 Git worktree 供多工人并行改码。
  - **Board 硬隔离、Tenant 软命名**：board 各自独立 DB/workspaces/logs；解析顺序 `--board` > `HERMES_KANBAN_BOARD` > `~/.hermes/kanban/current` > `default`。
- **命令 / 配置 / 代码**：

  ```bash
  hermes kanban init
  hermes gateway start                     # 内嵌 dispatcher
  hermes kanban create "调研 AI 融资报告" --assignee researcher
  hermes kanban list && hermes kanban watch
  hermes kanban block <id> "需要 API Key"
  hermes kanban boards create team-a --switch
  ```

  ```yaml
  kanban:
    dispatch_in_gateway: true
    dispatch_interval_seconds: 60
  ```

  路径：`~/.hermes/kanban.db`（默认库）、`~/.hermes/kanban/boards/<slug>/kanban.db`（多板隔离）、`~/.hermes/kanban/current`（CLI 当前板指针）。

- **坑与边界**：
  - 卡片长期停在 `Ready` 几乎都是 Gateway 没跑（dispatcher 不存在），先 `hermes gateway status` 再怀疑工人。
  - 工人 401 是 Profile 维度问题，对该 assignee 单独跑 `hermes -p <name> model`；主 Profile 配好不代表其它也配好。
  - Dashboard 用浏览器 `localStorage` 记当前板，**不会**改写 CLI 的 `current` 指针，两边各自独立。
  - Kanban 不替代 Git 分支策略（仍需 worktree + 人工 review）；父 Agent 只要一轮摘要时用 `delegate_task` 更省 token。
- **原文**：https://agent.jeffjade.com/hermes-agent/kanban-multi-agent-board/

### 高级特性

- **一句话**：Voice、Vision、浏览器、`execute_code`、`delegate_task`、ACP 六块能力都只是 toolset 与 backend 的组合，共同代价是 token 与系统依赖，共同纪律是默认不全开、按平台勾选。
- **机制/要点**：
  - **Voice 是可降级的 STT 链**：优先级本地 faster-whisper（无 Key）→ Groq Whisper → OpenAI Whisper → Mistral Voxtral；TTS 默认 Edge TTS 同样免 Key。`/voice on` 后 `Ctrl+B` 录音（键位 `voice.record_key`），静音约 3 秒断句，连续 3 次无有效语音退出；Whisper 对静音的幻听短语会被过滤。Discord 还支持语音频道双向对话（需 `discord.py[voice]` + Opus）。
  - **Vision 的价值在「路径化」**：图片落盘 `~/.hermes/images/`，`vision_analyze` 吃文件路径而非剪贴板，才能与 `browser_snapshot` 串成 UI 比对；`/paste` 比 `Ctrl+V` 稳（后者文本优先）。
  - **浏览器靠无障碍树而非像素**：快照里元素带 `@e1` 类 ref，结构化且省 token；默认开启的混合路由在访问 `localhost`/内网时自动切**本地 sidecar**，公网仍走云——为的是云浏览器够不到本机开发服。
  - **`execute_code` 是上下文压缩器**：脚本经 Unix socket RPC 调白名单工具（`web_search`、`web_extract`、`read_file`、`write_file`、`search_files`、`patch`、`terminal` 仅前台），**只有 `print()` 回上下文**；故「3 次以上且有过滤/分支/循环」时收益最大。不可递归 `execute_code`、`delegate_task`、MCP；`project` 模式同 cwd 同 venv，`strict` 用临时隔离目录 + 固定 `sys.executable`；默认超时 300s、stdout 50KB、每轮 50 次 RPC，子进程环境剥离含 `KEY`/`TOKEN`/`SECRET` 的变量。
  - **子 Agent 是全新会话**：看不到父对话，只靠 `goal` 与 `context`；并行用 `tasks=[...]`，默认最多 3 并发；叶子子 Agent 强制禁用 `delegation`、`clarify`、memory 写、`code_execution`、`send_message`，要多层编排得 `role="orchestrator"` 并抬高 `max_spawn_depth`（默认 1 即扁平）。
  - **ACP 是协议适配层**：VS Code/Zed 经 stdio JSON-RPC 驱动 `hermes acp`，日志走 stderr、stdout 留给协议；cwd 绑定**编辑器工作区**而非 server 进程；`hermes-acp` toolset 刻意不含 messaging 与 cronjob。
- **命令 / 配置 / 代码**：

  ```bash
  pip install "hermes-agent[voice]" && pip install "hermes-agent[acp]"
  # macOS: brew install portaudio ffmpeg opus；Debian: apt install portaudio19-dev ffmpeg libopus0
  hermes acp --check && hermes acp --setup-browser --yes
  ```

  ```yaml
  code_execution: { mode: project, timeout: 300, max_tool_calls: 50 }   # project | strict
  delegation: { max_concurrent_children: 3, max_spawn_depth: 1, child_timeout_seconds: 600 }
  browser: { cloud_provider: browserbase, auto_local_for_private_urls: false }  # 默认 true
  ```

  ```python
  from hermes_tools import web_search
  print(web_search("Rust async 2025", limit=5))   # 只有 print 回上下文

  delegate_task(goal="修复 api/handlers.py 第 47 行 TypeError",
                context="NoneType 无 get 属性；项目 /home/user/myproject；Python 3.11",
                toolsets=["terminal", "file"])
  ```

- **坑与边界**：
  - Voice/Vision 的主要成本是系统依赖（portaudio、ffmpeg、libopus）而非 Key；SSH 远程终端上贴图与麦克风通常不可用，应本地操作或先传图再 `/image`。
  - 云浏览器 + 流式 TTS 叠在默认主模型上会显著抬价，辅助任务应走 `auxiliary` 或子 Agent 分流。
  - 无人值守长任务别嵌套 `delegate_task`（受 `child_timeout` 约束、无 PTY），改用独立 `hermes chat -q` 进程或 Gateway `/background`。
  - ACP 的 `list/load/resume` 只作用于当前 ACP 进程内的会话管理器，与 `hermes --continue` 的持久化路径相关但不等同。
- **原文**：https://agent.jeffjade.com/hermes-agent/advanced-features/

## 跨章节关联

- **册内互指**：让 Agent 无人值守有三条路——Gateway 的 Cron 与 `/background`、独立 `hermes chat -q` 进程（高级特性）、Kanban dispatcher 派活。技能章的 `[[as_document]]` 依赖 Gateway 出站渲染，无 Gateway 时该指令无效。钩子的 `pre_tool_call` 与《工具系统》的 `approvals.mode`、hardline 构成三层：钩子管组织策略、审批管逐次确认、hardline 管不可绕过的地板。
- **与本站其他章节**：《记忆、学习与 Skill》给 Level 0/1/2 与 Curator 的概念模型，本册《技能系统实战》落到 frontmatter 与 `pin`/`restore` 运维；《配置与个性化》的 Profile 隔离是 Kanban 具名工人的前提；《插件系统（Plugins）》是 Plugin 钩子载体（`plugins.enabled` 未开则 `pre_tool_call` 静默失效）；《安全、性能与最佳实践》承接 Dashboard 远程访问加固；《架构拆解》解释 API Server 与 ACP 这两个非聊天入口如何复用同一内核。
- **对应通用 Harness 概念**：代理循环（流水线与钩子插槽图是同一循环两视图）；工具（toolset 是能力开关单位，`hermes-acp` 是按场景裁剪的范例）；权限（allowlist/pairing + admin 分层 + approvals + hardline 四层不可合并）；上下文（`execute_code` 只回 print、子 Agent 只回摘要、注入走用户消息而非 system，目标都是控制进上下文的东西）；记忆（Skill 是程序性记忆，`kanban.db` 是程序性任务的持久化记忆，`session_search` 是检索式记忆）；Skill（渐进披露 + 信任级别 + Curator 淘汰构成需运维的生态）；子代理（同步阻塞 vs 异步具名两种拓扑）；MCP（提供新工具而非策略，`execute_code` 内不可调是刻意白名单）；沙箱（`terminal.backend` 决定落点，`dir:/abs/path` 与 `strict` 模式是另两处显式隔离）。

## 自测（3 题）

1. 你把 bot 给了同事，他先收到「未授权」，加 `GATEWAY_ALLOW_ALL_USERS=true` 后能用，但随后执行了 `rm`。两层配置各该怎么设？为何官方不推荐全放开？
   - 提示：接入层用平台 `*_ALLOWED_USERS` 或 DM pairing；命令面用 `allow_admin_from` + `user_allowed_commands`。全放开等于把带 `terminal` 的 bot 交给任何能找到它的人，破坏性命令只剩 approvals/hardline 兜底。
2. 一个自写 Skill 三个月没用，`/deploy-runbook` 已不在 Level 0 索引；另一个 Hub 装的 Skill 一直没动。解释成因与恢复手段。
   - 提示：Curator 确定性规则 30 天 `stale`、90 天入 `.archive/`，但跳过 `.hub/lock.json` 与 `.bundled_manifest`，故 Hub Skill 免疫、自写 Skill 不免疫；恢复用 `hermes curator restore`，预防用 `hermes curator pin`。
3. Shell 钩子与插件都对 `terminal` 做 `pre_tool_call` block，报错却只来自插件；而把同样逻辑放进 Gateway 钩子则完全不生效。为什么？
   - 提示：`pre_tool_call` 的 block 先注册者优先（Python 插件先于 Shell），多个 block 只第一个生效；Gateway 钩子只在 `gateway:*`/`session:*`/`agent:*`/`command:*` 观测点触发、无 `pre_tool_call` 语义，且在 CLI 下根本不触发。
