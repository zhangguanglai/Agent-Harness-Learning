# Claude Code · 第二部分 快速上手（安装与配置）

> 本分册整理自智能体漫游（agent.jeffjade.com），覆盖章节：
> - 安装、登录与环境配置 — https://agent.jeffjade.com/claude-code/installation-setup/
> - 基于第三方 API 使用 Claude Code — https://agent.jeffjade.com/claude-code/third-party-api/
> - 配套工具精选 — https://agent.jeffjade.com/claude-code/companion-tools/

## 本册要点

- 安装方式是地基：原生脚本把二进制装到 `~/.local/bin/claude` 并自带后台静默更新；`sudo npm install -g` 会让 `~/.claude/` 权限错乱，连带 resume、自动更新、Hooks/Skills 一起坏。
- 认证只有两条正路：浏览器 OAuth（token 落 `~/.claude/`）或 `ANTHROPIC_API_KEY`（shell 变量、`settings.json` 的 `env` 块、`/config`）；免费 claude.ai 账户没有 Claude Code 权限。
- 接第三方模型只需两个变量：`ANTHROPIC_BASE_URL` + `ANTHROPIC_API_KEY`，端点只要兼容 Anthropic Messages API；三档模型再用 `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL` 映射到第三方模型 ID。
- 配置优先级链是「项目 `.claude/settings.json` > 全局 `~/.claude/settings.json` > shell 环境变量 > 默认值」，但 shell `export` 常压过 GUI 写入，改完必须在新终端用 `/status` 复核。
- 多会话并行按四层补工具：CC Switch 管连接、`git worktree` 做目录隔离、tmux/zellij 保活分屏、lazygit 审大 diff——按痛点装，不必全装。

## 逐章笔记

### 安装、登录与环境配置

- **一句话**：从零装到可用，并在装的那一刻就把"以后能否自动更新、能否恢复会话"这类长期问题定下来。
- **机制/要点**：
  - 两条安装路线：**原生二进制**（脚本探测平台、下载独立可执行文件，不依赖 Node，装在用户目录无需提权，更新由 CLI 自己的进程完成）与**包管理器**（brew/winget/apt/dnf，更新权交给系统，默认不自动升级）。
  - 原生安装的自动更新不是"重启才检查"：启动时查、运行中定期查，后台下载，**下次启动才生效**，所以不会打断正在跑的会话。
  - Homebrew 用两个 cask 表达发布节奏：`claude-code` 跟 stable（约延迟一周，跳过有重大回归的版本），`claude-code@latest` 跟 latest；`CLAUDE_CODE_PACKAGE_MANAGER_AUTO_UPDATE=1` 可让 CLI 代跑升级。
  - `npm install -g` 被官方标记为不推荐，根因是 npm 包只是壳：靠可选依赖拉平台二进制、postinstall 做符号链接，真正跑的 `claude` 与 Node 无关，多一层壳只多一份风险。
  - 致命点是 `sudo`：会话状态、配置、Memory、Hooks、Skills 全在 `~/.claude/`，root 与普通用户混权后写入失败是**静默**的——恢复会话、覆盖二进制更新都会失效，而报错点出现在别处。
  - 认证与账户门槛：需 Pro / Max / Team / Enterprise 或 Console 账户；token 持久化在 `~/.claude/`，因此认证状态与目录绑定，换账号靠 `claude logout`，多项目则各配 key。更新通道有 `latest`（默认）与 `stable`，入口 `/config` → Auto-update channel。
- **命令 / 配置 / 代码**：

```bash
# 原生安装（macOS / Linux / WSL）；Windows PowerShell：irm https://claude.ai/install.ps1 | iex
curl -fsSL https://claude.ai/install.sh | bash
curl -fsSL https://claude.ai/install.sh | bash -s 2.1.89   # 固定版本 / 指定通道
claude --version   # 期望 2.x.x
claude doctor      # 诊断 PATH / 权限 / 更新状态
claude update      # 手动强制更新

# 只能用 npm 时：prefix 到用户目录，绕开 sudo 造成的 ~/.claude 混权
npm config set prefix '~/.npm-global'
export PATH=~/.npm-global/bin:$PATH
npm install -g @anthropic-ai/claude-code
```

```json
// ~/.claude/settings.json
{ "env": { "ANTHROPIC_API_KEY": "sk-ant-api03-..." } }
```

- **坑与边界**：
  - `sudo npm install -g` 后的混权是最常见的"玄学故障源"，症状是功能失效而非明确报错——先 `ls -l ~/.claude` 看所有者。
  - 包管理器装完误以为会自动更新：须 `brew upgrade claude-code` / `winget upgrade Anthropic.ClaudeCode`，或显式设 `CLAUDE_CODE_PACKAGE_MANAGER_AUTO_UPDATE=1`。
  - `command not found` 多是 PATH 问题：原生安装确认 `~/.local/bin` 在 PATH，包管理器安装需重开终端让 shim 生效。
  - 免费 claude.ai 计划不含 Claude Code，账户/网络还需在 Anthropic 支持的国家与地区；不满足只能走第三方 API（下章）。本章尾部「禁用自动更新 / IDE 客户端 / 终端最佳实践 / 卸载」抓取被截断，对应配置键名（原文未明确）。
- **原文**：https://agent.jeffjade.com/claude-code/installation-setup/

### 基于第三方 API 使用 Claude Code

- **一句话**：把"必须连 Anthropic"变成"兼容 Messages API 的端点都行"，用两个环境变量替换模型供给方。
- **机制/要点**：
  - 需求先分类：地区限制、成本控制、模型自由（同时用 Gemini/DeepSeek）、统一账单、合规网关；已有原生 Key 且能直连时，不引入第三方才是最稳方案。
  - 这是内置能力而非 hack：`ANTHROPIC_BASE_URL` 改请求去向，`ANTHROPIC_API_KEY` 改凭证；网关把同格式请求转给实际模型、响应原路返回，Claude Code 侧无感知——协议形状就是"厂商可替换"的接缝。
  - 变量名不随厂商变：接 DeepSeek、OpenRouter 仍叫 `ANTHROPIC_*`，因为客户端只认这套契约，凭据治理因此可以"换值不换名"。
  - 模型身份靠别名：内部按 opus / sonnet / haiku 三档选模型，`ANTHROPIC_DEFAULT_*_MODEL` 把三档指向第三方模型 ID，`CLAUDE_CODE_SUBAGENT_MODEL` 可单独给子代理指定模型（成本与能力分层）。
  - 兼容度决定功能上限：端点缺 Messages API 特性（如 `tool_use` 的流式返回）时，工具调用类高级功能降级——这是"能跑"与"跑得对"的分水岭，不是配置错误。
  - 网关分三档：托管聚合（OpenRouter，多厂商路由 + 用量与价格面板）、官方兼容端点（DeepSeek 提供 `/anthropic`）、自建代理（LiteLLM 或 Gemini 转换代理：数据不出内网、一个端点挂模型池、账单统一）。
- **命令 / 配置 / 代码**：

```json
// ~/.claude/settings.json：DeepSeek 兼容端点 + 三档别名；换 OpenRouter 只改 URL 与 key
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
    "ANTHROPIC_API_KEY": "sk-...",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-v4-flash",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "deepseek-v4-pro",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "deepseek-v4-pro[1m]"
  }
}
```

```bash
claude --model anthropic/claude-sonnet-4-6     # 会话内也可 /model 切换
# 自建 LiteLLM 网关（一个端点挂多模型，再指向 localhost:4000/anthropic）
litellm --model claude-sonnet-4-6 --model gemini/gemini-2.5-pro
alias cc-deepseek='ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic" ANTHROPIC_API_KEY="sk-xxx" claude'
echo ".claude/settings.json" >> .gitignore      # 项目级配置防泄漏
```

- **坑与边界**：
  - 只改一半：换了 key 忘换 `ANTHROPIC_BASE_URL`（或反之），请求仍打向 `api.anthropic.com`，报 401/404，容易误判成 key 失效。
  - 兼容性假设：DeepSeek 端点**可能不支持全部** Messages API 特性（原文点名 `tool_use` 流式返回）；Gemini 因 Google 无官方 Anthropic 格式端点，必须经第三方代理转换。
  - 密钥进 Git：项目级 `.claude/settings.json` 写 key 后必须 `.gitignore`，更稳的是环境变量间接引用；企业 SSO/OAuth 场景仍需 Enterprise 或 Console 权限，普通第三方接入用环境变量即可。
  - 直连优先：多一跳就多一处超时、限流与计费口径差异，`API_TIMEOUT_MS`（示例给到 3000000 量级）正是为长任务跨网关留余量。本章「推荐环境变量」代码块与后半段（参数说明、FAQ、方案选择）抓取被截断，`ANTHROPIC_AUTH_TOKEN` 与 `ANTHROPIC_API_KEY` 的取舍（原文未明确）。
- **原文**：https://agent.jeffjade.com/claude-code/third-party-api/

### 配套工具精选

- **一句话**：CLI 只解决"单个会话能干活"，换商、并行、断线、审 diff 这四类摩擦要用四层外部工具补。
- **机制/要点**：
  - 工具按**层**而非按名字组织：连接（换提供商/故障转移）、隔离（同 repo 多任务）、编排（终端保活分屏）、审阅（大 diff 把关）；层间自由组合，只装缺的那层。
  - CC Switch 是"配置的状态机"：把 `settings.json` 里多套 `env` 变成可点选的"当前提供商"，附带 Local Routing、自动故障转移与 MCP/Skills/Prompts/用量的统一界面；它不改协议，只把手工编辑产品化，因此必须与"只保留一处真相"配套。
  - `git worktree` 是**文件系统级隔离**：同一 `.git` 对象库挂多个工作目录，每目录绑一条分支、各跑一个 `claude`，未提交文件与 `git status` 互不污染；代价是每个 worktree 各自装依赖、磁盘翻倍。
  - tmux / zellij 解决**进程与终端的绑定期**：CLI 随终端关闭或 SSH 断线而结束，可 detach 的 session 让长任务活过断线，pane 让"左编辑右 agent、上日志下测试"同屏成立；tmux 生态成熟，zellij 上手轻。
  - lazygit 解决**审阅带宽**：Agent 一次改十几个文件时，线性 `git diff` 易漏，TUI 的文件树 + hunk 级 stage 把"接受哪些改动"变成逐块勾选，防误 stage 生成物与密钥。
  - 进阶工具按场景叠：`fzf`/`zoxide`/`direnv`/`atuin` 管终端与 env，`rg`/`fd`/`bat`/`yazi` 管检索浏览，`gh`/`watchexec`/`just`/`pre-commit` 管流程闭环，MCP server 打通数据库与 GitHub，`bottom`/`lnav` 管观测。优先顺序：direnv → gh → watchexec → 项目适配的 MCP server。
- **命令 / 配置 / 代码**：

```bash
# 隔离层：并行任务各开一个工作目录
git worktree add ../myapp-feature-a -b feature/a
cd ../myapp-feature-a && claude
git worktree remove ../myapp-feature-a   # 清理；未合并分支 -D 会丢提交
```

```bash
# 编排层：可 detach 的会话 + 分屏（tmux 按 Ctrl+b 再按 % 竖分）
tmux new -s claude-dev
tmux attach -t claude-dev
zellij -s claude-dev                     # Ctrl+o 切 pane；zellij attach claude-dev
```

```bash
# 连接层：先确认 shell 变量没压过 GUI 写入，进会话用 /status 核对路由；审阅层 hunk 级 Stage
env | grep ANTHROPIC
unset ANTHROPIC_BASE_URL ANTHROPIC_API_KEY
lazygit
```

- **坑与边界**：
  - 双真相：`~/.zshrc` 的 `export ANTHROPIC_*` 与 CC Switch 写入并存时改了不生效；长期方案是只留一处（GUI 管或 shell 管）。
  - CC Switch 支持多 CLI 目标（Claude Code、Codex、Gemini CLI 等），改配置前先确认"当前管理的目标"没选错；企业已下发 managed settings 禁本地改配置时不适用。
  - worktree 不是沙箱：仍共享同一 `.git`、同一用户权限与网络，只隔离工作文件；真隔离看「沙箱隔离机制」。
  - lazygit 只是提交前本地审阅，不等于 PR review，团队 gate 仍需 CI/CD。本章「失败模式 / 决策边界 / 继续读下一章之前」抓取被截断，失败模式清单（原文未明确）。
- **原文**：https://agent.jeffjade.com/claude-code/companion-tools/

## 跨章节关联

- **「第一个会话」**：本章解决"装好、通得上、能并行"，会话章解决"进去之后怎么下指令、怎么授权"；`~/.claude` 权限直接决定 `-c` / `-r` 恢复能否用。
- **「代理循环与工具」**：第三方 API 章改的是循环里"调用哪个 LLM"这一环；端点不支持 `tool_use` 流式，等于工具调用环节被削弱，症状是"Agent 变笨"而非报错。
- **「MCP 协议」「Skills 技能」「Hooks 机制」**：三者的配置与脚本都落在 `~/.claude/` 与项目 `.claude/`，是所有写入型扩展的共同地基，`npm sudo` 的污染会同时打穿它们。
- **「SubAgents」「Agent Teams 与多会话协作」**：`CLAUDE_CODE_SUBAGENT_MODEL` 对应"子代理用哪个模型"；worktree + tmux 是 Agent Teams 的低配版编排。
- **「沙箱隔离机制」「安全边界与权限心智」**：worktree 隔离文件、tmux 隔离会话，都不隔离进程权限与网络，别把 worktree 当安全机制。
- **「CLI 与配置查阅」「常见问题排查」**：`settings.json` 优先级、`claude doctor`、`/status` 的实测口径在排障章；本章给的优先级链遇 shell 变量要实测校正。
- **Harness 概念映射**：安装 = 运行时基座；第三方 API = 模型接入层（循环的 LLM 端点）；`settings.json` + `CLAUDE.md` = 配置与记忆入口；CC Switch = 配置注入的产品化；worktree = 执行环境隔离的轻量形态；lazygit = 变更进主干前的人工闸门（对应权限里的"人工批准"）。

## 自测（3 题）

1. 同事用 `sudo npm install -g` 装完后 `claude -r` 恢复不了历史会话，`claude doctor` 却版本正常。根因在哪？
   - 提示：`~/.claude/` 有 root 所有的文件，普通用户进程写不进去；恢复会话、后台更新、Hooks/Skills 写入都走这条路。修法：改原生安装，或 `npm config set prefix ~/.npm-global` 重装。
2. 接上 DeepSeek 网关后 `/model` 仍是 Claude 名字、工具调用偶发失败，先查哪两处？
   - 提示：一是 `ANTHROPIC_BASE_URL` 是否真指向 `/anthropic` 端点（只换 key 会静默走原生）；二是该端点对 Messages API / `tool_use` 的兼容度，并用 `ANTHROPIC_DEFAULT_*_MODEL` 把三档映射到具体模型。
3. 两个 Claude 会话在同一仓库互踩改动，最小改动量的修法是什么，还要补什么？
   - 提示：`git worktree add` 各挂目录 + 分支，分别 `claude`，用 `git status` 互不污染验证；env 差异用 direnv，断线保活上 tmux/zellij，审 diff 用 lazygit。
