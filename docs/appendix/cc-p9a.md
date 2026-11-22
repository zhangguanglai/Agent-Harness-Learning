# Claude Code · 第九部分 进阶实践（目标、自动化、调试、成本）

> 本分册整理自智能体漫游（agent.jeffjade.com），覆盖章节：
>
> - `/goal` 与跨轮持续目标 —— https://agent.jeffjade.com/claude-code/goal-mode/
> - Routines 与定时自动化 —— https://agent.jeffjade.com/claude-code/routines-automation/
> - 调试与错误恢复 —— https://agent.jeffjade.com/claude-code/debug-error-recovery/
> - Token 成本感知与会话经济学 —— https://agent.jeffjade.com/claude-code/token-economics/

## 本册要点

- `/goal` 把"继续"外包给一个**独立评估模型**（small fast model，默认 Haiku）：它只读完整对话 + 你的条件，判 yes/no，判否则理由即下一轮指令——所以条件必须能从聊天记录里被裁决。
- 四层"让会话继续干活"要在同一张表里分清：`/goal`（条件续跑）、`/loop`（时间间隔）、`/schedule`（会话内 cron）、Routines（Anthropic 云端，关终端也跑）；前三层都依赖本机终端开着。
- 跑偏后的正确第一步不是补一句话，而是 `/diff`/`git diff` 看实际改动，再按决策树选 `/rewind`、`/compact`、`/clear` 或降级 Plan Mode；同一 bug 纠正三轮仍失败优先换会话。
- 成本主要是**上下文占用**的函数：`/context` 看占用、`/usage` 看花费、`/status` 看策略来源，长任务每约 15 分钟对一次表，比事后看账单便宜。
- 降本靠收窄输入而非少干活：Range Read、限定目录 Grep、把专项规范拆进 `.claude/rules/` 的 `paths` frontmatter（按需加载）、`/mcp` 停用无用服务器、`/effort low` + Sonnet。

## 逐章笔记

### /goal 与跨轮持续目标

- **一句话**：你说一次可验证的终点，Claude 跨多个 turn 自主续跑，直到另一个模型确认达标或你手动清除。
- **机制/要点**：
  - 默认代理循环每轮结束都把控制权交回人，多步任务的真实成本是"打字说继续"十几次；`/goal` 把这一步系统化，条件本身即首轮指令，不需要额外 prompt。
  - 双角色分离：主模型（执行者）读文件改代码跑命令；评估者是配置的 small fast model（默认 Haiku），只读"完整对话 + 条件"，输出 yes/no 和简短理由，不自己跑命令、不读仓库。这是与"模型自认做完就停"的本质差别——执行者无法自证完工。
  - 判否时理由直接成为下一 turn 的指导；判是则 goal 清除、transcript 记 achieved、控制权归还。官方把 `/goal` 定义为 session-scoped 的 prompt-based Stop hook。
  - 状态可观测：界面出现 `◎ /goal active` 指示器带计时，无参数 `/goal` 可看条件、运行时长、turn 数、token 消耗与评估者最新理由。
  - 生命周期绑定会话：每会话仅一个活跃 goal，新 `/goal` 覆盖旧的；`/clear` 或开新对话会清除；`--resume`/`--continue` 会恢复**未完成**的 goal，但 turn 计数、计时器、token 基线重置。条件上限 4000 字符。
  - 独立性来自 hooks 体系：仅在已接受工作区信任对话框时可用，企业 managed 设置可禁用；命令会给原因而非静默失败。
- **命令 / 配置 / 代码**：
  ```bash
  # 最小路径：条件即指令，立刻开跑
  /goal all tests in test/auth pass and the lint step is clean

  # 带约束（禁止改测试文件）
  /goal Migrate API v1 to v2 in src/services, update all call sites, \
        ensure all existing tests still pass, do not modify test files outside src/services

  # 带上限，防止无界续跑
  /goal Implement feature X per DESIGN.md, all acceptance criteria met, or stop after 15 turns

  /goal          # 查看条件/时长/turn 数/token/评估理由
  /goal clear    # 提前结束（别名 stop、off、reset、none、cancel）

  # 非交互：跑到条件满足或 Ctrl+C
  claude -p "/goal CHANGELOG.md has an entry for every PR merged this week"
  ```
- **坑与边界**：
  - 评估者不看仓库：测试输出没写进 transcript，它可能判"未完成"，于是明明做完还在续跑。要强制"每轮贴命令与退出码"。
  - 条件写成 `migration done`、`clean up the code` 这类无命令可裁决的说法，等于把裁决权交给模型的语气，易误判完成。
  - 长跑会累积上下文腐烂与 token：token 飙升、停不下来时应加上限、拆小 goal、必要时 `/compact` 或换会话。
  - 页面记录社区有人报告单次 goal 续跑数小时且费用很高，但明确说明这类数字因仓库与模型而异（原文未明确具体数值），长任务先小规模试跑。
- **原文**：https://agent.jeffjade.com/claude-code/goal-mode/

### Routines 与定时自动化

- **一句话**：把"谁在什么时刻启动下一轮"拆成四层——条件、间隔、会话内 cron、云端 cron，选错层就会在关终端时白等。
- **机制/要点**：
  - 四层的判别轴只有两个：**触发源**（上一轮结束 / 时间到 / cron / 外部事件）和**运行位置**（本机会话 vs Anthropic 托管云）。本机三层都要求终端开着，笔记本合盖或休眠即中断。
  - `/goal` 与 `/loop` 的差别不是频率而是触发条件：前者"上一 turn 结束且评估未达标"，后者"间隔到时"。所以"每 5 分钟重试一次部署"该用 `/loop`，不该用 `/goal`。
  - `/loop` 是 bundled skill，在同一次交互会话里按间隔重复同一提示，直到目标达成或人停止；Scheduled tasks 提供会话内的 cron 式调度工具，与 `/loop` 互补；Desktop 另有 desktop scheduled tasks。
  - Routines 跑在 Anthropic 基础设施上，关机仍执行，可由 cron、API 调用或 **GitHub 事件**触发；入口有 Web、Desktop、CLI 的 `/schedule`（或 `/routines`，以 `/` 菜单实际项为准）。
  - 确定性排序：Hooks/脚本最高（必跑、几乎无模型费）> GitHub Action `@claude`（workflow 定义清晰）> Routines（依赖模型判断）> `/loop`（人在环、本机 token）。合 main 的硬门禁应交给 CI，不要交给 Routine。
- **命令 / 配置 / 代码**：
  ```text
  /loop 5m 重跑 pnpm test test/auth，失败就贴最后 50 行失败栈
  /schedule            # CLI 向导创建会话内定时任务 / Routine（或 /routines）
  ```
  ```text
  选择顺序（页面给出的确定性排序）
    Hooks / 脚本       确定性最高，几乎无模型费  → 高风险操作的必跑拦截
    GitHub Action @claude  高，workflow 定义清晰 → 合 main 前的硬门禁
    Routines           中，依赖模型判断        → 每日 review、依赖审计、GitHub 事件
    /loop              低，人在环              → 当场调试时按间隔重试
  ```
- **坑与边界**：
  - 把 `/loop` 当 cron 用：终端一关就停，长间隔任务必须上 Routines 或 CI。
  - 账号能力限制：仅 Bedrock/Vertex/Foundry 凭证时，Routines、部分 Web 功能、Code Review 等可能不可用，需要 Anthropic API 或 Teams 席位；发布前逐项对照功能页。
  - 无人值守定时任务若无审查直接写仓库，等于让模型每晚改坏主干；稳妥做法是只读分析 + 开 PR。
  - 频率过高是费用主因，降频或换更便宜的模型比优化提示更有效。
- **原文**：https://agent.jeffjade.com/claude-code/routines-automation/

### 调试与错误恢复

- **一句话**：跑偏后最贵的动作是"再发一条纠正消息"，正确顺序是先看实际改动、再判定失败类型、最后选回滚/压缩/换会话/重规划之一。
- **机制/要点**：
  - 补丁式纠错会留下三份互相冲突的上下文——你最初的意图、模型错误的改动加上自信总结、你事后追加的否定。模型于是要么重复同类工具调用，要么为圆谎继续加改，要么把错方案写进压缩摘要，成本随轮次复利。
  - 失败信号要按机制归类，而不是按报错：循环打转（重复 Read 同一文件、Grep 范围越扩越大、diff 很小）、幻觉式确信（引用没 Read 过的文件、"已修复"却无测试输出）、工具权限死锁（同一 Bash 权限反复弹、被拒后换等价命令撞墙）、上下文失真（忘掉 CLAUDE.md 禁止项、autocompact thrashing、偏离已批准计划）。
  - 恢复动作按"是否还做同一任务"和"改动范围是否可控"两轴分流：`/rewind` 回检查点（对话和/或工作区）、`/compact` 带聚焦说明压缩历史、`/clear` 换任务或策略破产、Plan Mode 冻结改码重新规划。
  - Hook 的 `deny` 早于 `allow` 生效，所以被 Hook 拒后继续重试等价操作是纯浪费；该改提示或改规则。
  - 记忆是止损装置：重复纠正同一件事时写进 CLAUDE.md 或 `.claude/rules/`，把"降低概率"交给记忆、"确定性拦截"交给 Hooks。
- **命令 / 配置 / 代码**：
  ```bash
  /diff                 # 或 git diff / git status，先看真实改动
  /context              # 判断该 rewind、compact 还是 clear
  /rewind               # 别名 /undo、/checkpoint；部分版本 Esc 连按两次
  /compact Focus: failing test auth.middleware.test.ts, last good commit abc1234, do not touch migrations/
  /plan                 # 或 Shift+Tab，方向错了就别继续改码
  /clear                # 清对话，CLAUDE.md 与项目文件仍在
  /doctor               # CLI 起不来、升级后异常
  claude --resume       # 列出可恢复会话
  ```
  ```markdown
  ## 已知 Agent 误区（持续更新）
  - 2026-03: 曾把 session TTL 改在 middleware；正确位置是 src/services/session.ts
  - 改 API 前必须先 Read OpenAPI 片段 tests/fixtures/openapi/auth.yaml
  ```
- **坑与边界**：
  - Checkpoint 不跟踪你在 IDE 里手改的文件，**不能**代替 Git；范围不可控时直接 `git checkout`/`stash`。
  - `Esc` 连按两次打开同类界面只在部分版本成立（原文限定"部分版本"），不能当作跨版本保证。
  - `/clear` 前应 `/rename` 给会话命名，否则旧轨迹难用 `/resume` 找回。
  - `/compact` 对 thrashing 无效——压缩刚结束又被大输出灌满时，该用子代理隔离探索或开新会话。
- **原文**：https://agent.jeffjade.com/claude-code/debug-error-recovery/

### Token 成本感知与会话经济学

- **一句话**：把上下文占用当成一等指标来读数——`/context` 看占用、`/usage` 看花费、`/status` 看策略来源，然后用收窄输入的方式降本。
- **机制/要点**：
  - 三个监测命令分工不可混：`/context` 网格展示当前会话占用与哪些记忆/工具过重；`/usage`（别名 `/cost`、`/stats`）给本会话 token、本地估算费用与套餐限额；`/status` 打开设置 Status 页看版本、模型、账号与设置来源（含 Enterprise managed），它不替代 `/context`。
  - 每轮循环都要重送一整包输入：对话历史（随轮次线性增长）、CLAUDE.md 与 `.claude/rules/`、工具输出（单次可达极大）、MCP 工具定义（未用的服务器也占）、子代理摘要。所以"扫一遍 monorepo"往往比"改十个文件"贵得多——前者把大量片段塞进每一轮的底座。
  - 固定开销与浮动开销要分开治：没有 `paths` 的规则每轮都在，属于固定开销；测试日志、整文件 Read 属于浮动开销。前者靠拆规则，后者靠读范围和 Hook 预处理。
  - 订阅用户的 `/usage` 中 Session 费用是本地估算、不等于账单，权威用量看 Claude Console Usage；后台会话摘要等仍有少量 token（官方说明通常每会话不足约 0.04 美元量级）。走 Bedrock/Vertex/Foundry 时 Console 指标可能不全，需在云侧对账。
  - 压缩与换会话是两种成本策略：`/compact` 保留任务语义、丢掉噪声；`/clear` + 短提示 + `@` 关键文件让执行段只背少量文件。
- **命令 / 配置 / 代码**：
  ```bash
  /context      # 当前占用明细
  /usage        # 本会话 token / 本地估算费用 / 限额（/cost、/stats）
  /status       # 版本、模型、账号、设置来源
  /btw 什么是 autocompact  # 旁路提问，不污染主线
  /compact Keep: auth middleware decision, failing test name, files already edited. Drop: early repo-wide grep results.
  /mcp          # 停用不用的 MCP 服务器，省工具定义
  /model        # 简单任务选 Sonnet，难题再用 Opus
  /effort low   # 简单任务降推理档位
  ```
  ```markdown
  ---
  paths:
    - "src/api/**/*.{ts,tsx}"
    - "tests/**/*.test.ts"
  ---
  # API 层约定
  - 新 endpoint 必须补 integration test
  ```
- **坑与边界**：
  - 把 Session 估算费用当账单，或反过来完全不看 `/usage` 直到变慢才发现，两种极端都会失控。
  - `/status` 常被误认为用量视图，它看的是配置与策略来源，查占用还得 `/context`。
  - 无 `paths` 的规则和 `.claude/CLAUDE.md` 一样在启动时全量加载，规则堆多即每轮固定加价。
  - opusplan 等混合策略会在规划与执行阶段切换底层模型；网关接入时必须用 `ANTHROPIC_DEFAULT_OPUS_MODEL`、`ANTHROPIC_DEFAULT_SONNET_MODEL` 映射到真实 model ID，否则模型别名可能指错。
- **原文**：https://agent.jeffjade.com/claude-code/token-economics/

## 跨章节关联

- **同站前置**：`/goal` 的建筑块来自 [Hooks 机制](https://agent.jeffjade.com/claude-code/hooks/)（它是 session-scoped 的 prompt-based Stop hook）与 [代理循环与工具](https://agent.jeffjade.com/claude-code/agent-loop/)；Routines 一章把 [Slash 命令](https://agent.jeffjade.com/claude-code/slash-commands/) 里的 `/loop`、`/schedule` 与云端能力合并成四层对照。
- **恢复动作的落点**：[Plan Mode](https://agent.jeffjade.com/claude-code/plan-mode/)（方向错时的降级）、[上下文管理与多代理](https://agent.jeffjade.com/claude-code/context-management/)（`HANDOFF.md` 冷启动、子代理隔离）、[CLAUDE.md 编写与维护](https://agent.jeffjade.com/claude-code/claude-md-authoring/)（错误模式与 `paths` 规则的归属）、[Slash 命令](https://agent.jeffjade.com/claude-code/slash-commands/)（`/clear` 与 `/compact` 别混用、`/btw` 旁路）。
- **成本的下游**：Token 一章与 [调试与错误恢复](https://agent.jeffjade.com/claude-code/debug-error-recovery/) 共用同一棵决策树，只是把判据从"质量"换成"花费"；组织级手段见 [团队与组织级落地](https://agent.jeffjade.com/claude-code/team-organization/) 与 [生态深度集成](https://agent.jeffjade.com/claude-code/ecosystem-integration/)；网关模型别名见 [基于第三方 API](https://agent.jeffjade.com/claude-code/third-party-api/)。
- **与通用 Harness 概念的映射**：
  - **代理循环**：默认循环每轮归还控制权，`/goal`、`/loop`、`/schedule`、Routines 是四种不同的"续跑驱动器"，本质都在改写循环的终止条件（条件 / 间隔 / cron / 事件）。
  - **权限**：`/goal` 依赖工作区信任对话框与企业 managed 设置，auto mode 只放开 turn 内批准、不跨 turn；Hook 的 deny 优先于 allow，是权限链的上游。
  - **上下文与记忆**：CLAUDE.md、`.claude/rules/`、子代理摘要、MCP 工具定义都是每轮固定成本；`/compact` 与 `/clear` 是两种不同的记忆重置语义（保留任务 vs 清空历史）。
  - **工具与子代理**：Reader/Reviewer 双会话、只读子代理、子代理跑重探索只回摘要，是同时降低幻觉与成本的同一手段。
  - **MCP / Skill / 沙箱**：`/mcp` 停用服务器省工具定义；专项规范可下沉为 Skill 而非常驻规则；Routines 在云端跑，其隔离与权限边界由托管侧决定（原文未明确其沙箱细节）。

## 自测（3 题）

1. `/goal` 的评估者为什么不能替代"再问一遍"式的自我检查？它在什么情况下会误判？
   提示：它只读完整对话 + 条件，不跑命令、不读仓库；测试输出未进 transcript 则判未完成，条件模糊则误判完成。修法是要求每轮贴命令与退出码，并把条件写成可裁决的终态 + 验证方式 + 约束。

2. 同一 bug 纠正三轮仍失败，为什么应优先 `/clear` 或新开会话，而不是继续追加消息或只做 `/compact`？
   提示：追加消息是在已污染的历史上继续推理，错误改动与"自信总结"仍在窗口里，且每轮为纠错重付全文 token；`/compact` 保留任务语义但对 thrashing 无效，压缩完可能立刻又被灌满。换会话则让执行段只背 `@` 关键文件。

3. 要让"每天九点审计 main 的依赖漏洞"真正每天九点跑，应该选哪一层，为什么不是 `/loop`？
   提示：选 Routines（云端 cron，关机仍跑，可由 GitHub 事件或 API 触发）；`/loop` 与会话内 `/schedule` 都要求本机终端开着，休眠即断。若必须作为合 main 的硬门禁，则交给 CI 的 GitHub Action，因为确定性最高。
