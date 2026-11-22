# Hermes Agent · 第一部分 入门

> 本分册整理自智能体漫游（agent.jeffjade.com），覆盖章节：
> - **认识 Hermes Agent** — https://agent.jeffjade.com/hermes-agent/what-is-hermes-agent/
> - **安装与环境准备** — https://agent.jeffjade.com/hermes-agent/installation-setup/
> - **第一次对话** — https://agent.jeffjade.com/hermes-agent/first-conversation/

## 本册要点

- Hermes 的基本单元是 **Agent Loop**（组提示 → 调模型 → 执行工具 → 回灌结果 → 再调模型），不是一轮问答；CLI、TUI、消息网关、Cron、ACP 共用同一个 `AIAgent`，差异只在入口适配层。
- 「越用越强」不是模型权重变化，而是四类**外部工件**在积累：`MEMORY.md`/`USER.md`、`SKILL.md`、`state.db`（FTS5 会话检索）、Curator 归档。
- 安装的验收标准是「`hermes` 可执行 + `hermes doctor` 无阻塞项」；`command not found` 九成是 PATH 或 Shell 未 reload，与 Hermes 无关。
- 换模型两个入口别混用：会外 `hermes model`（新增 Provider / OAuth / 写 Key），会内 `/model`（只在**已配置**的模型间切换）。
- 启动校验硬门槛是 **≥64K tokens 上下文**；自托管模型必须显式调大，否则被拒或在中途耗尽窗口。

## 逐章笔记

### 认识 Hermes Agent

- **一句话**：它把「一轮问答」换成「一个能长期驻留、自己写 Skill、自己维护记忆的执行体」，价值在多轮跨会话的经验沉淀，而不在单次回答质量。

- **机制/要点**：
  1. 主循环固定：接收意图 → 组系统提示 → 调模型 → 若返回 tool calls 则执行并把结果塞回上下文 → 再调模型，直到产出最终回复并持久化会话。
  2. 三项能力必须叠加：约 70 项内置工具（以当前 registry 为准，MCP 另计）让回复基于真实命令输出；跨会话状态（记忆文件 + SQLite）保留环境事实；程序性记忆把复杂步骤固化成可复用手册。
  3. 记忆用**冻结快照**（frozen snapshot）注入：会话开始时把磁盘上的记忆块打进系统提示，中途仍可写盘但当前会话看不到变化。换来提示前缀稳定与 Prompt Caching 命中，代价是新内容下一次会话才生效。
  4. Skill 走**渐进加载**：系统提示只放索引层（名称 + 描述，文档称 Level 0），需要时 `skill_view` 拉全文。官方建议在「完成复杂任务 / 踩坑后找到可行路径 / 被用户纠正」时用 `skill_manage` 写回，常以「5 次以上工具调用」为复杂任务示例；Curator 周期性把未用 Skill 标 stale 或归档到 `~/.hermes/skills/.archive/`，防止技能库膨胀。
  5. 运行形态不绑本机：终端后端有 local、docker、ssh、daytona、modal、singularity 等，换后端等于换**命令执行的影响范围**（本地 shell / 容器隔离 / 远程主机），Agent 核心不变。

- **命令 / 配置 / 代码**：

  ```bash
  hermes curator pin <skill>     # 保护关键 Skill，免被归档
  hermes curator run --dry-run   # 预览归档/合并动作
  hermes skills install <name>   # Hub 技能只能由用户安装，Agent 不能自行拉取
  ```

  ```text
  入口（CLI / Gateway / Cron / ACP）→ AIAgent：组 prompt → 调模型 → 执行工具 → 循环
    ├─ Tools / Skills   终端、文件、网页、浏览器 + SKILL.md 渐进加载
    ├─ Memory           MEMORY.md / USER.md，冻结快照
    └─ Sessions         SQLite + FTS5（~/.hermes/state.db）
  ```

  | 维度 | Hermes | IDE Copilot / 网页助手 | LangChain 等框架 |
  | --- | --- | --- | --- |
  | 主要界面 | CLI、TUI、20+ 消息平台、Cron | 编辑器内或网页 | 需自建服务 |
  | 跨会话记忆 | 内置文件 + SessionDB + 可选插件 | 通常弱或仅会话级 | 自行接向量库 |
  | 工作流沉淀 | 一等公民 Skill + Curator | 少见 | 自行设计闭环 |

- **坑与边界**：
  - 「自改进」不是模型变聪明；真正风险是 Agent 自己写错了记忆或 Skill，你得有发现与回滚路径。
  - 外部记忆插件（Honcho、Mem0）与内置 `MEMORY.md`/`USER.md` 是**并存**关系，不是替换，别以为接上就统一了记忆层。
  - 它不替代 IDE 原生方案：深度改本地 monorepo 用 Claude Code、Cursor Agent 更顺；且默认调云端模型 API，禁出网环境须先确认合规（条款原文未明确）。

- **原文**：https://agent.jeffjade.com/hermes-agent/what-is-hermes-agent/

### 安装与环境准备

- **一句话**：本章唯一验收标准是「`hermes` 可执行 + `hermes doctor` 无阻塞项」；装完立刻配 Gateway 只会把配置问题放大。

- **机制/要点**：
  1. 前置只有四条：Python 3.11+、Git（git 路径必需，`git --version` 能跑即可）、安装期可访问 PyPI/GitHub、数百 MB 磁盘；Node.js 22、ripgrep、ffmpeg 由安装器按需装。
  2. 安装方式决定**更新节奏**：git 脚本跟踪 `main`；pip 装 PyPI 发布标签，不含每天提交；原生 Windows 为早期 beta，数据在 `%LOCALAPPDATA%\hermes\`，与 WSL 下 `~/.hermes/` 独立可并存。
  3. 装完必须 reload shell 或开新终端：新写入的 `~/.local/bin` 不进当前进程 PATH，`command not found` 由此而来。
  4. `hermes doctor` 汇总安装来源、缺失依赖与配置问题并给修复建议，通常点明是哪类安装；`hermes update` 据此打印匹配的更新命令，无需手设环境变量。
  5. `hermes postinstall` 用于补齐 pip 安装缺失的运行时（Node、Playwright/Chromium）并引导基础配置；git 脚本已内置，除非 doctor 提示才补跑。多实例隔离靠 Profile：`hermes -p 名称` 指向不同 `HERMES_HOME`。

- **命令 / 配置 / 代码**：

  ```bash
  curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
  source ~/.zshrc                    # 或 ~/.bashrc，必须 reload
  hermes --version && hermes doctor
  pip install hermes-agent && hermes postinstall        # 版本跟 PyPI 标签
  iex (irm https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.ps1)   # 此行是 PowerShell，原生 Windows beta
  curl -fsSL .../install.sh | bash -s -- --skip-browser # 无 sudo / 不要浏览器
  hermes config check && hermes config migrate          # 升级后处理旧字段
  hermes update                                         # 按布局提示更新方式
  ```

  | 安装方式 | 代码位置 | `hermes` 命令 | 用户数据 |
  | --- | --- | --- | --- |
  | git 脚本 | `~/.hermes/hermes-agent/` | `~/.local/bin/hermes` | `~/.hermes/` |
  | pip | site-packages | `~/.local/bin/hermes` 等 | `~/.hermes/` |
  | git 脚本（root） | `/usr/local/lib/hermes-agent/` | `/usr/local/bin/hermes` | 各用户仍有 `~/.hermes/` |

  `~/.hermes/` 与 `~/.hermes/hermes-agent/` 是两层：前者是数据（`config.yaml`、`.env`、`memories/`、`skills/`、`state.db`），后者只是代码。

- **坑与边界**：
  - `ModuleNotFoundError: dotenv` 之类：说明用系统 Python 跑了仓库内脚本，应走 venv 内 launcher（`~/.hermes/hermes-agent/venv/bin/hermes`）。
  - root 与用户级安装混用而不理清 PATH，会让 `~/.hermes` 因权限错乱变成半坏状态。
  - 无 sudo 的部署账号：Chromium 系统库需管理员执行 `npx playwright install-deps chromium`；不跑浏览器自动化可直接 `--skip-browser`。
  - 生产服务器别默认 local 终端后端；隔离属「工具系统」「安全、性能与最佳实践」章节（具体配置键原文未明确）。

- **原文**：https://agent.jeffjade.com/hermes-agent/installation-setup/

### 第一次对话

- **一句话**：一次成功的对话等于「Provider 认证正确 + 上下文足够 + 工具后端可达」，而 `hermes doctor` 并不保证这三者同时成立。

- **机制/要点**：
  1. 密钥与配置**物理分离**：API Key / Token 写 `~/.hermes/.env`，非秘密项写 `~/.hermes/config.yaml`；用 `hermes config set` 写入可避免 Key 被提交进 yaml。
  2. `hermes model` 与 `/model` 是两个层级：前者在会话外的 shell 里跑完整向导（新增 Provider、OAuth、设备码、自定义 OpenAI 兼容端点），后者只在**已配置**的 Provider/模型间热切换；要新增从未配过的 Provider，只能退出会话重来。
  3. 64K 是启动校验的硬门槛：窗口过小会被直接拒绝，或在多步工具调用中迅速耗尽；自托管须按后端单独调大（Ollama 常见 `OLLAMA_CONTEXT_LENGTH=65536`，llama.cpp 用 `-c 65536`，LM Studio 用 `--context-length 64000`），压缩/视觉等侧任务可交给 auxiliary 模型，首轮保持 `auto`。
  4. 会话恢复依赖正常落盘：会话写入 `~/.hermes/state.db`（SQLite + FTS5），异常杀进程可能丢最后一轮；`hermes --continue`/`-c` 回到**最近一次**，跨 Profile 或多机时易进错，用 `hermes sessions list` 核对。这与 `MEMORY.md` 属不同层。
  5. Slash 由中央 `COMMAND_REGISTRY` 注册，CLI 与消息网关共用一套定义，故两端行为一致，已装 Skill 会注册成动态 Slash（如 `/plan`）；`@` 引用则是发送前把内容展开进用户消息的另一条通路，与对话中 `session_search` 查历史互补。

- **命令 / 配置 / 代码**：

  ```bash
  hermes model          # 会话外：配置 Provider（OAuth / Key / 自定义端点）
  hermes --tui          # 现代 TUI（模态层、鼠标、非阻塞）；不带参数即经典 CLI
  hermes --continue     # 或 -c，恢复最近会话；hermes sessions list 列历史
  hermes config set model anthropic/claude-sonnet-4-6
  hermes config set OPENROUTER_API_KEY sk-or-...
  ```

  ```text
  /status  model/provider/session ID/工作目录/token 统计   /model  已配置模型间切换（--global 写回 config）
  /new [名称]  /history  /save  /usage  /tools  /compress [主题]  /resume <标题>  /quit（--delete 删记录）
  /steer 请关注 auth 模块      # 当前工具执行完再注入，不打断工具循环
  @file:src/main.py:10-25   @folder:src/components（≤200 项）   @diff  @staged  @git:5（≤10）
  # 首条消息要可核对；验收：横幅 model/provider 与配置一致、无认证报错、真调用了
  # terminal 并引用输出、连续 2~3 轮上下文连贯
  检查当前工作目录，列出顶层文件，并指出最像主入口的文件名。
  ```

- **故障速查**：401 或空回复 → 重跑 `hermes model`、查 `~/.hermes/.env`；报 context 过小 → 自托管默认 4k~8k，提到 ≥64K；工具失败 → `/status` 看 backend，docker daemon 未起是常见原因。

- **坑与边界**：
  - 首轮未成功时别去配 Gateway：消息平台只会把 Provider 问题放大。
  - `/model` 不能替代 `hermes model` 做 OAuth；设备码流程只能在会话外完成；`/yolo` 跳过危险命令确认，只适合隔离环境。
  - `@` 会拒绝敏感路径（`~/.ssh/`、`~/.hermes/.env`）、工作区外与二进制文件；软限约 25%、硬限约 50% 上下文，大文件用行范围。

- **原文**：https://agent.jeffjade.com/hermes-agent/first-conversation/

## 跨章节关联

- **站内前后衔接**：本册往下接「记忆、学习与 Skill」（冻结快照、`skill_manage`、持久目标 `/goal`、Curator 在此展开）、「配置与个性化」（`config.yaml` 完整键位与 Profile）、「工具系统」（本章只验证工具可达，权限与 `terminal.backend` 真正配在这里）。再往后「消息网关」「事件钩子」「Kanban 多 Agent 看板」把单机能力外扩，「架构拆解」「从零实现类似 Agent」回头看实现。
- **与本站 Claude Code 专题的分工**：Claude Code 强在绑定代码库的高权限本地工程循环；Hermes 强在长期驻留、多平台触达、自写 Skill 与网关自动化。原文建议并存：仓库里用 Claude Code 改代码，VPS 上用 Hermes 跑定时巡检并把结果推到 Telegram。
- **对应通用 Harness 概念**：
  - *代理循环* → Agent Loop；CLI 只负责渲染与 Slash 分发，循环与 Gateway 共用 `AIAgent`。
  - *工具* → 约 70 项内置工具 + MCP 扩展，后端选择（local/docker/ssh/…）决定影响范围。
  - *上下文* → 三级预算：系统提示（Skill 索引 + 冻结记忆）常年占位、`@` 引用按需注入、`/compress` 与 auxiliary 模型压缩；64K 门槛源自 tool schema 与系统提示的固定开销。
  - *记忆* → 声明式（`MEMORY.md`/`USER.md`，有字符上限）+ 程序性（`SKILL.md`）+ 情节性（`state.db` + FTS5），三层互补而非替代。
  - *Skill* → YAML 前言 + Markdown，兼容 agentskills.io，Level 0 索引 + 按需 `skill_view`；第三方包只能人工确认安装。
  - *子代理* → 本册未涉及，见「Kanban 多 Agent 看板」「高级特性」。
  - *MCP* → 本册仅提到「内置工具之外另计」，接入见「工具系统」「插件系统」。
  - *沙箱* → `terminal.backend` 的 docker / daytona / modal / singularity 即执行边界；本册只给「生产别用 local」的结论。

## 自测（3 题）

1. **`hermes model` 与 `/model` 各管什么？何时必须退出会话？**
   提示：前者是会外向导（新增 Provider、OAuth、写 Key、自定义端点），后者只切已配置模型；要新增从未配置过的 Provider，必须 `/quit` 后重跑 `hermes model`。

2. **记忆的「冻结快照」为什么这样设计，代价是什么？**
   提示：会话开始时把 `MEMORY.md`/`USER.md` 打成快照注入系统提示，中途写盘不改当前会话，换来提示前缀稳定与 Prompt Caching 命中；代价是本次改动下次会话才生效。

3. **启动报 context 过小、或对话半小时后变慢变傻，分别该查什么？**
   提示：前者是自托管 context 不足（需 ≥64K，`OLLAMA_CONTEXT_LENGTH` / `-c` / `--context-length`）；后者用 `/usage` 看占用、必要时 `/compress`，工具失败再查 `terminal.backend` 与 daemon。
