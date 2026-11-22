# Claude Code · 第十部分 排障速查

> 本分册整理自智能体漫游（agent.jeffjade.com），覆盖章节：
>
> - CLI 与配置查阅 —— https://agent.jeffjade.com/claude-code/cli-and-settings-reference/
> - Claude Code 常见问题排查 —— https://agent.jeffjade.com/claude-code/troubleshooting-faq/

## 本册要点

- 出问题先跑 `/doctor`（CLI 起不来则终端跑 `claude doctor`）：它已覆盖安装版本、配置有效性、MCP 连接、上下文占用、记忆文件与子代理定义大小五个维度，比手动猜快。
- 配置生效看**来源**不看**内容**：`/status` 的 Setting sources 才是事实，同一份 JSON 写在项目层还是用户层结论可能相反。
- 认证类"登录循环"多半不是服务端：系统时钟偏差、macOS Keychain 损坏、遗留 `ANTHROPIC_API_KEY` 三件事按序查完，能解掉绝大多数 401/403。
- API 报错先分流：5xx/529 是服务端与配额无关（可换 `/model` 继续工作），429/余额是配额，`Prompt is too long` 是上下文——三者恢复动作完全不同。
- 上下文通胀是性能与质量问题的共同根因：`/compact`、`/context`、Range Read、子代理隔离、`/rewind` 是同一套工具的五个用法，不是五件事。

## 逐章笔记

### CLI 与配置查阅

- **一句话**：把散落在各章正文里的 CLI 标志、`settings.json` 顶层键与环境变量收敛成一页对照表，并把"改完为什么不生效"交给优先级链和 `/status` 裁决。
- **机制/要点**：
  - 页面自我定位是"查阅章，非跟读主线"：只收录本系列正文常引用的条目，**不全量同步官方**，因此天然滞后于版本，发版前要回官方 CLI reference / Settings / env-vars 做 spot-check。
  - 配置优先级（高到低）：managed settings → 项目 `.claude/settings.json` → 用户 `~/.claude/settings.json` → 环境变量；作者把这条链落在 `/status` 的 Setting sources 上，意思是层级只是规则，"当前实际吃的是哪一层"要用运行时状态验证。
  - CLI 标志按用途分成三簇，设计上刻意把"能力边界"与"运行形态"拆开：批处理形态（`-p/--print`、`--output-format json`）、权限收放（`--allowedTools`、`--disallowedTools`、`--permission-mode`、`--dangerously-skip-permissions`）、会话与执行控制（`--max-turns`、`--model`、`--continue/-c`、`--resume`、`--remote/--teleport`、`--worktree`）。拆分的好处是 CI 里只放开需要那一簇。
  - `settings.json` 的顶层键面向"团队共享层"：`permissions.allow/deny` 定工具规则，`permissions.disableBypassPermissionsMode` 让组织强制关掉 bypass 通道，`hooks` 挂生命周期脚本，`env` 注入变量，`model` 定默认模型，`autoUpdates`/更新通道 控版本策略，`sandbox` 关联隔离配置。
  - 模型别名（`opus`/`sonnet`/`haiku`/`opusplan`）不是硬编码到具体版本，而是由 `ANTHROPIC_DEFAULT_*_MODEL` 解析——换别名背后指向哪个模型是配置项，不是代码常量；`opusplan` 会同时读 opus 与 sonnet 两个变量。
  - 出站控制有两个语义不同的开关：`ANTHROPIC_BASE_URL` 把流量改道到自定义网关（第三方 API 唯一入口），`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` 只是压掉非必要出站；更新控制则拆成 `DISABLE_AUTOUPDATER` 与 `DISABLE_UPDATES` 两个变量。
- **命令 / 配置 / 代码**：
  ```bash
  # 非交互批处理：只放开读与测试，跑完即退，输出可被程序消费
  claude -p "解释 src/auth 的登录流程" \
    --allowedTools "Read,Grep,Bash(npm test)" \
    --output-format json --max-turns 6
  # 网关与别名解析：换别名背后的真实模型
  export ANTHROPIC_BASE_URL="https://your-gateway.example.com"
  export ANTHROPIC_DEFAULT_SONNET_MODEL="<网关实际提供的模型 ID>"
  export DISABLE_AUTOUPDATER=1
  ```
  ```json
  {
    "model": "sonnet",
    "permissions": {
      "allow": ["Read", "Grep", "Bash(npm test)"],
      "deny": ["Bash(rm -rf *)"]
    },
    "hooks": { "Stop": [] },
    "env": { "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1" }
  }
  ```
  （`permissions.disableBypassPermissionsMode` 的取值原文未给出，以官方 Settings 文档为准。）
- **坑与边界**：
  - 表是**节选**，只能当索引：表里没有的 flag 不代表不存在，也不代表同类开关都有对应物，直接查官方 CLI reference，别类推。
  - 环境变量优先级最低，却会静默压过 `/login` 的认证结果；IDE 终端从 `.env` 注入的旧 `ANTHROPIC_API_KEY` 是"登录成功仍报未授权"的高频根因。
  - 团队共享只该提交 `.claude/settings.json`：把密钥写进 `env` 段等于把密钥提交进 Git，密钥走环境变量或密钥管理。
  - 默认值有争议时以 `/status` 的实际生效值与官方 changelog 为准，不要信记忆里的旧默认值——这正是作者写"发版前 spot-check 三条你正在用的 flag"的原因。
- **原文**：https://agent.jeffjade.com/claude-code/cli-and-settings-reference/

### Claude Code 常见问题排查

- **一句话**：以症状为索引的排障地图——第一步永远是自检，然后按安装/登录、API 与限流、性能稳定性、搜索、配置、IDE 六条支线拿到最小恢复动作。
- **机制/要点**：
  - `/doctor` 是一次结构化环境自检：安装状态与版本、配置文件有效性、MCP 服务器连接、上下文使用情况、内存文件与子代理定义大小。它把"配置写了却不生效"类问题从玄学变成可枚举项；命令根本起不来时降级为终端 `claude doctor`。
  - 认证排查是一条因果链而非清单：OAuth token 有有效期，系统时钟偏差大就会让刚发的 token 立刻被判过期；token 存 macOS Keychain，条目损坏则表现为登录循环；而 `ANTHROPIC_API_KEY` 优先级高于 `/login`，所以"浏览器授权成功、终端仍说未登录"通常是本地环境问题。
  - 错误码先分三类再动手：服务端故障（`500`、`Repeated 529 Overloaded`，与你的请求和配额无关，529 不计配额）、认证与配额（`Invalid API key`、`429`、`Credit balance is too low`、session limit）、输入过长（`Prompt is too long`）。客户端对临时故障会自动重试（最多 10 次、指数退避），因此**看到这些错说明重试已耗尽**。
  - 高 CPU/内存被明确判定"不是内存泄漏"：真实原因是上下文窗口里累积了过多文件内容与工具输出。thrashing 是更具体的一种——压缩确实成功了，但某个文件或工具输出立刻又把窗口填满，于是 Claude Code 主动停手，避免继续烧 API 调用。
  - 搜索失效的根因是内置 `ripgrep` 二进制在你的系统上跑不起来，解法是装系统级 ripgrep 并用 `USE_BUILTIN_RIPGREP=0` 让 CLI 改用系统二进制；WSL 跨 `/mnt/c/` 是另一类问题——文件系统性能差导致结果偏少，且 `/doctor` 此时仍报 Search 正常，属于自检盲区。
  - 回答质量下降不是报错而是配置漂移：模型被无意切小、`/effort` 变了、窗口接近满载丢掉早期约束、记忆文件过大吃掉上下文。作者强调 `/rewind` 常优于在当前线程纠正——错误的尝试留在历史里会锚定后续回答。
- **命令 / 配置 / 代码**：
  ```bash
  # CLI 启动不了时的自检；会话内用 /doctor
  claude doctor
  # 认证三查：遗留变量、时钟、连通性
  env | grep ANTHROPIC     # 是否有旧 key 压过 /login
  timedatectl status       # macOS 则看「日期与时间 → 自动设置」
  curl -I https://api.anthropic.com   # PowerShell 用 curl.exe

  # 搜索：改用系统 ripgrep
  winget install BurntSushi.ripgrep.MSVC   # macOS: brew install ripgrep
  export USE_BUILTIN_RIPGREP=0
  # 配置排查
  python -m json.tool .claude/settings.json
  claude --debug           # 完整的配置加载日志
  ```
  ```text
  # 会话内最小恢复动作
  /compact keep only the plan and the diff
  /context      # 看窗口占用分布，定位哪个 MCP 服务器或记忆文件最吃空间
  /rewind       # 回到出错前重来
  /mcp disable <name>
  ```
- **坑与边界**：
  - 两页对配置分层写法不一致：CLI 章为 managed → 项目 `.claude/settings.json` → 用户 `~/.claude/settings.json` → 环境变量；排障章写成 `.claude/settings.local.json` > `.claude/settings.json` > `~/.claude/settings.json`。（差异原文未解释，实测以 `/status` 的 Setting sources 为准。）
  - 不要用 `NODE_TLS_REJECT_UNAUTHORIZED=0` 绕 TLS 错误——那等于关闭全部证书校验；企业 CA 应设 `NODE_EXTRA_CA_CERTS` 指向 CA bundle。
  - 不要用 `sudo npm install -g`；EACCES 是全局目录权限问题，照 npm 官方权限修复指引处理，用 sudo 只会把权限问题埋更深。
  - thrashing 时先做分段读取或把大文件操作移进子代理，再考虑 `/compact`，否则压缩完立刻再满；同理 `/doctor` 不覆盖搜索性能与 Agent 行为层故障。
- **原文**：https://agent.jeffjade.com/claude-code/troubleshooting-faq/

## 跨章节关联

- **同站内的分工**：本册只承接"技术故障"（安装、网络、API 报错、配置不生效）；Agent 行为故障（循环打转、幻觉、上下文失真）与 `/rewind`、`/clear`、`/compact` 的决策树归 [调试与错误恢复](https://agent.jeffjade.com/claude-code/debug-error-recovery/)；成本侧归 [Token 成本与会话经济学](https://agent.jeffjade.com/claude-code/token-economics/)；权限与 bypass 的取舍归 [安全边界与权限心智](https://agent.jeffjade.com/claude-code/security-permissions/)；`sandbox` 键归 [沙箱隔离机制](https://agent.jeffjade.com/claude-code/sandboxing/)；`--remote/--teleport` 归 [远程会话与 Channels](https://agent.jeffjade.com/claude-code/remote-sessions-channels/)；`hooks` 排错归 [Hooks 机制](https://agent.jeffjade.com/claude-code/hooks/)；`ANTHROPIC_BASE_URL` 场景归 [基于第三方 API](https://agent.jeffjade.com/claude-code/third-party-api/)。本册是这些章节的"索引层"，不是替代品。
- **映射到通用 Harness 概念**：
  - **代理循环**：`--max-turns` 是循环轮次预算，`/compact` 与 thrashing 是循环内的上下文回收策略；"压缩后立刻被填满"本质是循环拿到了新的超大工具输出。
  - **工具**：`--allowedTools` / `permissions.allow|deny` 是工具白黑名单，MCP 服务器的工具定义本身也占 token（`/mcp disable` 即卸载这部分工具面）。
  - **权限**：`--permission-mode`、`--dangerously-skip-permissions`、`disableBypassPermissionsMode` 构成三层收放——个人开关、组织强制、运行时状态。
  - **上下文**：`/context`、`Prompt is too long`、thrashing、`/compact` 是同一件事的不同表现；Range Read、子代理隔离、`.gitignore` 排除构建目录都是"减少进入窗口的字节"。
  - **记忆 / Skill / 子代理**：`/doctor` 会量内存文件与子代理定义大小——记忆和 Skill 不是免费的，它们与工具输出争同一窗口。
  - **MCP 与沙箱**：MCP 连接状态是 `/doctor` 自检项之一；`settings.json` 的 `sandbox` 键把隔离策略也纳入同一份可审计配置。
  - **可观测性**：`/doctor`（体检）、`/status`（生效来源）、`/context`（占用分布）、`/usage`（配额）与 `claude --debug`（加载日志）构成排障观测面——先取状态再改配置，顺序反了就会改到不生效的那一层。
  - **凭据与网关**：OAuth token / macOS Keychain / `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` 说明认证来源本身带优先级，凭据属于宿主环境而非会话状态，所以换机器或换终端会改变行为。
- **使用顺序**：先 `/doctor` 体检 → 按本册对应节取最小恢复动作 → 只有需要改配置时才回 `CLI 与配置查阅` 查键名 → 行为层故障（打转、幻觉、上下文失真）再转 [调试与错误恢复](https://agent.jeffjade.com/claude-code/debug-error-recovery/)。本册适合当书签，不适合从头读。
- **两个易混的切面**：`/model`、`/effort` 属于"配置漂移"，`/rewind`、`/clear` 属于"会话恢复"，同一次故障里常同时出现——先用 `/status` 确认配置来源，再决定是改配置还是退历史，不要用改配置去修一个已被错误尝试污染的会话。

## 自测（3 题）

1. **遇到问题时第一步运行什么？什么情况下这个命令用不了、该退到什么？**
   提示：`/doctor`（五维自检：版本、配置、MCP、上下文、记忆/子代理大小）；`claude` 命令本身起不来时在终端跑 `claude doctor`；连 `/doctor` 都看不出问题时用 `claude --debug` 看完整配置加载日志。
2. **`/compact` 与 thrashing 是什么关系——压缩成功了为什么还会 thrashing？**
   提示：压缩成功但某个大文件读取或工具输出立刻把窗口重新填满；Claude Code 检测到循环就停止重试以免浪费 API 调用。恢复顺序是分段读取/子代理隔离 → 带聚焦说明的 `/compact` → `/clear`，而不是反复压缩。
3. **`Invalid API key`、`Repeated 529 Overloaded`、`Prompt is too long` 三者该做什么不同的动作？**
   提示：分别属于认证、服务端过载（不计配额，可 `/model` 换模型继续或查 status.claude.com）、上下文超限（`/compact` 或 `/clear`，用 `/context` 看占用）；混为一类会用错恢复手段。
