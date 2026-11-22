# Claude Code · 第七部分 实战（SDK 与集成）

> 本分册整理自智能体漫游（agent.jeffjade.com），覆盖章节：
> - Agent SDK 程序化调用 —— https://agent.jeffjade.com/claude-code/agent-sdk/
> - CI/CD 与代码审查集成 —— https://agent.jeffjade.com/claude-code/ci-cd-integrations/
> - 远程会话与 Channels —— https://agent.jeffjade.com/claude-code/remote-sessions-channels/
> - Chrome 与 Web UI 测试 —— https://agent.jeffjade.com/claude-code/chrome-browser-testing/

## 本册要点

- Agent SDK 把代理循环变成库：`query({prompt, options})` 返回异步事件流，`allowedTools` + `maxTurns` + `max_budget_usd` 是无人值守场景的三道闸。
- GitHub Action 底层就是 SDK，`anthropics/claude-code-action@v1` 用 `prompt` 取代旧的 `direct_prompt`，CLI 参数一律塞进 `claude_args`。
- 没有官方集成时用 `claude -p` 兜底，务必显式写 `--allowedTools` 与 `--max-turns`，因为流水线里没人能点权限弹窗。
- Remote Control（`/rc`）续写的是**已有**本地会话，Channels 则把 CI、告警等外部事件反向推入会话，二者方向相反。
- 浏览器能力分两层：`/chrome`（Web，beta）与 Computer use（macOS GUI）；前端验收要写死"console 零 error"这类可判定条件。

## 逐章笔记

### Agent SDK 程序化调用

- **一句话**：把 Claude Code 的代理循环、工具与权限当作 Node/Python 库来驱动，用 `query()` 消费逐步事件，而不是拼一次性 prompt。
- **机制/要点**：
  - 三种调用方式按"谁掌控循环"分层：交互 CLI（人驱动）、`claude -p`（一次性 prompt 换一段输出）、Agent SDK（事件流交给你的代码）。只有 SDK 允许在循环中途按 message 类型分支处理，例如单独接管 tool call 或嵌进自己的 UI。
  - `query()` 返回 async iterable，`for await` 每轮拿到的是代理循环的一步（含工具调用事件）。这等于把 CLI 内部 loop 提升为公共 API，代价是你也要自己承担循环的退出条件。
  - 仓库内能力可整体复用：CLAUDE.md/rules、`.claude/skills/`、Hooks、`.claude/agents/`、`.mcp.json`。价值在于"本地调好的规范"能低成本搬到服务端；但密钥范围与网络出站必须在服务端另行收窄，不能靠本地配置兜底。
  - 成本由两层控制：turn 上限与美元预算（`maxTurns` / `max_budget_usd`），叠加 allow/deny 工具白名单与成本跟踪。三者是"最多跑几步 / 最多花多少钱 / 只能碰哪些工具"，缺一层就有失控面。
  - 行为与本地不一致时，根因通常不是模型而是上下文：未指定 `cwd` 与 settingSources 就不会加载仓库 CLAUDE.md，代理退化成"一个没有项目记忆的通用助手"。
  - 部署约束是密钥不进镜像层（环境变量或密钥管理服务），配合沙箱或容器隔离，可观测性接 OpenTelemetry。
- **命令 / 配置 / 代码**：

```
mkdir my-agent && cd my-agent
npm init -y
npm install @anthropic-ai/claude-agent-sdk
```

```ts
import { query } from "@anthropic-ai/claude-agent-sdk";

for await (const message of query({
  prompt: "List the files in the current directory",
  options: {
    maxTurns: 5,
    allowedTools: ["Read", "Glob", "Grep"],
  },
})) {
  console.log(message);   // 循环中的每一步事件
}
```

- **坑与边界**：
  - 包名与 API 面（`query` / `send` / `stream`）随版本演进，import 报错时先对官方 quickstart 与 TypeScript reference，不要凭记忆改导入名。
  - `allowedTools` 列得过窄会表现为"工具没执行"，很容易被误判成模型能力问题，实际是白名单把它挡在外面。
  - 不设 `maxTurns` 的自建服务等价于无预算上限，还需额外限制并发，否则一次重试风暴就能把账单打满。
  - SDK 不是无状态 chat API：它跑完整代理循环，会在宿主环境里真实读写文件，容器边界必须事先划好。
- **原文**：https://agent.jeffjade.com/claude-code/agent-sdk/

### CI/CD 与代码审查集成

- **一句话**：流水线里只有两条正路——官方托管集成（`@claude` 评论触发，底层是 SDK）或任意 CI 中的 `claude -p`；共同前提是最小权限密钥、可 review 的 diff、入库的 CLAUDE.md。
- **机制/要点**：
  - Action 的触发面是事件而非对话：`issue_comment`、`pull_request_review_comment` 上 `@claude`，或在 `pull_request` 事件里直接给 `prompt` 让它在 PR 打开时自动审查。
  - 安装有快慢两路：`/install-github-app` 本地向导自动配 GitHub App 与 `ANTHROPIC_API_KEY`，但需仓库管理员权限、且仅适用直连 Anthropic API；手动则是装 Claude GitHub App 并授予 Contents/Issues/Pull requests 读写 + 仓库 Secret + 复制 `examples/claude.yml` 进 `.github/workflows/`。
  - v1 相对 `@beta` 的差异是参数形态：`prompt` 取代 `direct_prompt`，CLI 参数一律收进 `claude_args`。用错版本不会报错，表现为参数被静默忽略。
  - 三样东西必须随代码入库：CLAUDE.md（Action 与本地同规范）、`.claude/skills/`（workflow 先 `checkout` 才能 `/skill-name` 调用）、workflow `permissions` 最小化。
  - 成本是 Actions 分钟与 API token 双计，可控手段是评论指令具体化、`claude_args` 里限 turn、concurrency 抑并发。
  - GitLab 与其他 CI 退到 `claude -p`：全局装 CLI、密钥进 CI/CD Variables（masked + protected）、`--allowedTools` 收窄到只读工具、`--max-turns` 封顶。
- **命令 / 配置 / 代码**：

```yaml
# .github/workflows/claude.yml（片段）
on: { issue_comment: { types: [created] } }   # 另可加 pull_request_review_comment
jobs:
  claude:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4             # 不 checkout 则加载不到 CLAUDE.md / skills
      - uses: anthropics/claude-code-action@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
```

```bash
npm install -g @anthropic-ai/claude-code          # GitLab 自建 runner
claude -p "审查 diff：列安全风险与测试缺口，输出 Markdown 列表" \
  --allowedTools "Read,Grep,Glob" --max-turns 10
git diff origin/main...HEAD | claude -p "列出安全风险，输出 JSON" \
  --output-format json --allowedTools "Read,Grep"  # 任意能跑 shell 的 CI
```

- **坑与边界**：
  - workflow 权限开太大，Action 会直接 push main。应限制为只推 PR 分支、合并留给人。
  - 密钥只走 `secrets.*` 与 CI Variables；写进 CLAUDE.md 或 workflow 明文是最高频的泄漏路径。
  - 指望流水线里有人点权限审批是设计错误——验收标准、工具白名单与 turn 上限必须提前写死；`@claude` 无响应先查 App 安装与 Secret，再看 Actions 日志。
  - Code Review 产品能力（多代理全库分析）与 Action 不是同一层，能力与 Plan 门槛以官方为准。
- **原文**：https://agent.jeffjade.com/claude-code/ci-cd-integrations/

### 远程会话与 Channels

- **一句话**：两个方向相反的远程能力——Remote Control 让人离开键盘而会话不断，Channels 让外部事件（CI、告警、聊天）进入会话。
- **机制/要点**：
  - Remote Control 的本质是**远程操纵已存在的本地会话**：`/remote-control`（别名 `/rc`）把会话接到 claude.ai/code 或移动端，你在外部设备看进度、回权限请求。它不是另起一个无状态 API 调用，所以上下文、已加载的仓库记忆、待批准的工具都还在。
  - Web 与终端可互迁：从 Web 启动的任务回本地继续用 `--teleport` / `--remote`，Web 版本身在云端沙箱里执行。
  - Channels 通过 **MCP Server** 声明能力，把 webhook、告警、聊天消息转成会话内 notification；模型看到事件后自行决定是否行动——这是"推入"而非"拉取"。
  - 自定义 Channel 要实现四件东西：capability 声明、notification 事件、reply 工具、sender 白名单。白名单是防止任意来源把指令塞进会话的关键。
  - Hooks 与 Channels 的差别是方向与确定性：Hooks 在工具调用前后拦截、脚本必跑（确定性高）；Channels 是外部→会话、模型选择性响应（确定性中）。
  - Slack 集成是成型的产品入口，Channels 是通用 webhook 契约。已在 Slack 生态的选前者，要接自建监控系统的选后者。
- **命令 / 配置 / 代码**：

```
# 让本地会话可从外部设备继续
/remote-control
/rc                 # 别名

# Web 任务与本地互迁
claude --teleport   # 或 --remote，按官方 cloud 文档
```

Channel 侧的配置入口（`/mcp` 或专用命令）与具体配置键随版本变化，原文未明确给出，实现自定义 Channel 时以官方 Channels reference 为准。

- **坑与边界**：
  - webhook 侧不做去重会让同一事件反复刷屏，节流要放在 Channel 侧而不是模型侧。
  - 会话 `/clear` 之后模型不再持有事件语境，行为会突然"失忆"；长流程会话应保持打开，或把规则写进 CLAUDE.md。
  - 异步事件 + 宽权限 = 误触发写操作。建议 plan 模式起步，并对出站动作显式 deny。
  - 敏感仓库要先确认企业是否允许会话出网，Remote Control 改变的是"谁在操作"，不改变数据流向。
- **原文**：https://agent.jeffjade.com/claude-code/remote-sessions-channels/

### Chrome 与 Web UI 测试

- **一句话**：让 Agent 自己打开浏览器跑完一遍前端流程，验收标准从"看起来对"换成"console 零 error + 截图/DOM 结论"。
- **机制/要点**：
  - Claude in Chrome（beta）把 Chrome 接到会话上：打开页面、读控制台、填表、抓数据。它补的是代理原本看不见的渲染层——前面的工具只能读源码与静态产物。
  - 启用路径有三步：装并登录支持 Chrome 的版本 → CLI 用 `/chrome` 配连接（VS Code 扩展走 `@browser`）→ 用自然语言下指令。
  - `/chrome` 与 `@browser` 只是入口差异，能力同源：命令行适合"终端 + 本地 dev server"，扩展适合"同屏改码 + 看 diff"。
  - Computer use 范围更大也更危险：它操作 macOS GUI（点击、输入、截屏），面向原生应用，不止浏览器；屏幕可见内容多、误点面大，团队应限制启用范围。
  - 验收必须可判定：要求回复里贴出 console 的 error 行，或在 diff 审查前对比截图/DOM 结论，否则"页面上看着没问题"会掩盖真实报错。
  - 一些体验项各自独立成官方页而非本章主管：`/voice [hold|tap|off]` 语音听写、`/fast`、`/statusline`、keybindings、fullscreen、output styles。
- **命令 / 配置 / 代码**：

```
/chrome     # CLI 中配置浏览器连接

# 提示词示例：动作 + 断言一起给
用浏览器打开 http://localhost:3000/login，填测试账号，点登录，
把控制台 error 列出来；如果非空就不要声称通过。
```

- **坑与边界**：
  - beta 能力遇真实站点登录、2FA 时常需人工介入；生产账号密码绝不能写进 prompt。
  - localhost 打不开先确认 dev server 是否在跑（如 `pnpm dev`），不要先怀疑浏览器连接。
  - 只看截图不看 console 会误判 UI 通过，必须要求贴 error 行。
  - Computer use 的提示越含糊越容易误点：缩小操作窗口并保留人工确认环节。
- **原文**：https://agent.jeffjade.com/claude-code/chrome-browser-testing/

## 跨章节关联

- **第七部分 · 生态深度集成**：本册是它的执行层补充——该章讲组织层策略与生态位，本册讲 SDK、CI、远程与浏览器的具体接法。
- **第七部分 · 完整实战工作流**：分工边界清晰——本地 `gh` 适合交互提交，Action 适合 PR 留痕、异步实现与定时任务。
- **第三部分 · 代理循环与工具**：SDK 的 `query()` 事件流就是该章代理循环的外部化形态；`maxTurns` 对应循环的步数上限。
- **第四部分 · 项目记忆**：CLAUDE.md 既是本地记忆，也是 CI 与 SDK 行为的"一致性来源"；没入库就是"审查结果与本地不符"的根因。
- **第五部分 · Skills / Plugins / SubAgents / MCP**：SDK 与 Action 都能加载 `.claude/skills/`；Channels 本质是 MCP Server 的一种用法；Code Review 的多代理分析落在 SubAgents 语义上。
- **第五部分 · Hooks 机制**：与 Channels 构成"确定性 vs 选择性"的一对，本册「远程会话与 Channels」一章给了方向对照。
- **第九部分 · 沙箱隔离机制 / 安全边界与权限心智**：SDK 部署的隔离、CI 的 `permissions`/`allowedTools`、`/rc` 的会话出网、Computer use 的误点，全部收敛到这一章的边界心智。
- **第九部分 · Token 成本与会话经济学**：`max_budget_usd`、`--max-turns`、concurrency 是这套成本模型在工程上的落点。
- **通用 Harness 概念映射**：Agent SDK = 代理循环作为可编程接口；`allowedTools` = 工具面白名单；`maxTurns`/`max_budget_usd` = 循环预算；`claude -p` = 无状态单轮调用；Channels = 外部事件驱动循环的入口；Remote Control = 会话状态跨设备延续（记忆）；`/chrome`、Computer use = 把浏览器/GUI 变成工具；`@browser` 与 `.claude/skills/` = 上下文与 Skill 载体的服务端复用。

## 自测（3 题）

1. **要在自家 Node 服务里嵌一个"改文件 + 跑测试 + 回 PR 评论"的代理，选 SDK 还是 `claude -p`？**
   提示：选 SDK。需要多轮、事件级分支与嵌 UI 时只有 `query()` 的事件流够用；同时必须显式给 `allowedTools`、`maxTurns`、`max_budget_usd`，否则服务端等于无限权限加无限预算。
2. **Action 里 `@claude` 能触发但总是改动别的东西、甚至直接推 main，先查什么？**
   提示：先查 workflow 的 `permissions` 与 `claude_args`，再看 CLAUDE.md 是否已入库。推 main 属于权限过大而非模型越界；行为与本地不一致则多半是仓库规范没跟着 checkout 进去。
3. **CI 失败想让当前本地会话直接收到通知，Remote Control 还是 Channels？**
   提示：Channels。Remote Control 是"人离开键盘去远程操纵已有会话"，不产生事件源；Channels 才是 webhook → MCP Server → 会话内 notification 的推入路径，且要做去重与 sender 白名单。
