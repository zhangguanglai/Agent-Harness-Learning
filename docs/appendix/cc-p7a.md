# Claude Code · 第七部分 实战（提示与工作流）

> 本分册整理自智能体漫游（agent.jeffjade.com），覆盖章节：
> - 高效提示工程的秘诀 —— https://agent.jeffjade.com/claude-code/prompt-engineering/
> - Claude Code 完整实战工作流 —— https://agent.jeffjade.com/claude-code/complete-workflow/
> - 上下文管理与多代理架构 —— https://agent.jeffjade.com/claude-code/context-management/
> - Claude Code 与生态的深度集成 —— https://agent.jeffjade.com/claude-code/ecosystem-integration/

## 本册要点

- 提示的验收条款比措辞重要：把测试命令、期望结果、可量化指标写进提示，Agent 才能自己迭代到"绿"并自行停止，这是页面所称的单点最高杠杆。
- 用 BMAD（Brief / Material / Action / Detail）在发送前做 30 秒自检，能消掉大部分返工；粒度目标是消除歧义，不是把提示写长。
- 任务阶段化：理解 → 设计（Plan Mode 批准）→ 实现（带自测）→ 审查（另一个会话），且审查必须换会话或换只读子代理，避免自写自审。
- 长任务的真正风险是窗口静默失真，应对顺序是固定剧本：重探索进子代理、`/context` 到约 70% 时 `/handoff` 落盘、再 `/clear` 冷启动。
- 团队落地按"本机 / 仓库 / 组织"三层分开做，仓库层把 `CLAUDE.md` 与 `.claude/settings.json`（deny 规则）入库并与 CI 同级 review。

## 逐章笔记

### 高效提示工程的秘诀

- **一句话**：把"聊天式提问"改造成一份代理任务书——结果、边界、验收、节奏齐备，Agent 才知道往哪个方向转、何时停。
- **机制/要点**：
  - 聊天框与代理环境的四个差异决定了提示结构：默认视野是整个 repo（而非你粘贴的片段）、结束条件是测试与 diff 收敛（而非输出一段文字）、你是拍板权限的指挥者、上下文成本含每次 Read 的文件与 Bash 输出。缺"验收"就没有停机判据。
  - 可验证性是杠杆点：给出测试、命令、截图对比或通过标准后，代理循环从"等你肉眼挑错"变成"自己试错收敛"；没给验证方式时，它每错一次都要一次人工往返。
  - BMAD 是发送前的心智检查表：B 写做完后世界有什么可观察变化（不是"读某文件"）、M 给 `@` 文件与报错原文、A 声明这轮是只读/规划/实现/审查、D 写禁止项与完成定义。四个标签不必真写进提示。
  - 规划段的价值与成本挂钩：能用一句话描述 diff（重命名、加一行日志）时跳过 Plan Mode 更省；方案不确定、多文件联动、模块陌生时才值得付规划成本。批准前可用 `Ctrl+G` 在编辑器里直接改计划文本。
  - 上下文策略是"给对不给满"：`@` 让 CLI 注入文件内容，比"请读 package.json"可靠；全库摸底此类高体积中间结果委派给子代理，主会话只留结论与下一步，否则早期的约束会被后来的 Read 噪声挤出注意力。
  - 页面把"Agent 迷茫"整理成可诊断信号：反复读同一批文件=目标不清；改了无关文件=范围未限；测试红了继续乱改=验收太弱；一直调查不落地=缺 Action。每种信号对应补哪一个提示要素是明确的。
- **命令 / 配置 / 代码**：

```
# BMAD 自检模板（修 bug）
【B】修复登出后会话仍有效的问题。
【M】@src/auth/session.ts @tests/auth.logout.test.ts  <失败日志>
【A】先复现失败测试，再改实现，不要先大范围重构。
【D】只改 auth 相关文件；用项目现有 vitest 命令；修完跑相关测试全绿。
```

```
# 大功能先让 Agent 采访你，产出 spec 再换会话实现
我想做：<一两句描述>。用 AskUserQuestion 详细采访我：实现、UI/UX、边界、风险与权衡。
不要问泛泛的问题。采访结束后把完整 spec 写到 SPEC.md。

# 会话命名与恢复（供 Writer / Reviewer 双会话配合）
/rename oauth-migration
claude --resume
```

- **坑与边界**：
  - "故意模糊"只适用于探索性提问（如"这个文件你会改进什么"），一旦要动代码必须收窄到 BMAD，否则得到的是满屏 diff。
  - 量化验收才可判定：如"覆盖率 62% → 80% 以上，只算 `src/auth/`""主包体积压到 200KB 以下"；"看起来好了"是模型自停的常见借口。
  - 同一会话对同一问题纠正超过两次仍失败时，继续堆补丁只会稀释上下文，应 `/clear` 后用更强的初始提示重来。
  - 页面在"可复用提示模板"的重构模板之后（含 `CLAUDE.md` 生成、issue→PR、常见反模式、调试小节）被截断，本节未覆盖其细节（原文未明确）。
- **原文**：https://agent.jeffjade.com/claude-code/prompt-engineering/

### Claude Code 完整实战工作流

- **一句话**：上一章讲"怎么说"，本章讲"按什么顺序做"——把理解、设计、实现、验证、审查、提交六段做成可组合链条，并按任务类型选入口。
- **机制/要点**：
  - 六段没有唯一正确顺序，但每一段对应固定的权限姿态与停止条件：理解段只读、设计段在 plan 模式产出可批准计划、实现段 `default`/`acceptEdits`、验证段把命令写死在提示里、审查段换第二个会话或只读子代理、提交段交给 `gh` 与 worktree。
  - 开工前有三件"一次性"准备，它们决定后续每轮的 token 效率：在仓库根启动（子目录启动会让默认视野变小）、`/init` 生成 `CLAUDE.md` 后人工删减到只剩"Agent 猜不到的命令、测试方式、禁止路径"、用 `/permissions` 给 `npm test`、`npm run lint` 之类低风险命令配 allow 而对 `git push`、`rm` 保持 ask/deny。
  - 工作流 A（理解陌生库）的产出必须落盘：概览 → 按领域深挖 → 追一条真实调用链 → 写进 `CLAUDE.md` 或 wiki，留在长会话里的结论一次 `/clear` 就没了。
  - 工作流 B（调试）的关键是顺序约束而非技巧：先复现并让 Agent 说明复现方法、确认根因前不许改码；再按二分判断是测试、实现还是 fixture 问题；随后"先红后绿"——先写失败用例，再改实现，只到该用例转绿且不破坏其它用例。
  - 工作流 C（重构）靠安全网：重构前 `git commit`/`git stash` 保证可 diff 可回滚，一次只动一个模块或一种机械替换，测试命令写进提示而非靠记忆；实验性大改可用 `claude --worktree` 隔离。
  - 工作流 D（新功能）把审批点前置：`/plan` 产出的计划必须含范围、步骤、决策、验收四要素，缺项就要求补齐再 `ExitPlanMode`；批准后才进实现段。
- **命令 / 配置 / 代码**：

```
# 一次性准备
cd /path/to/your-repo && claude
/init          # 生成 CLAUDE.md 后人工删减
/permissions   # 低风险命令 allow，git push / rm 保持 ask 或 deny

# 规划段与审批
claude --permission-mode plan     # 或会话内 Shift+Tab 切到 plan
/plan 为公开 API 增加按 IP 的速率限制：列出要改的文件、中间件插入点、测试策略与回滚方式。不要改代码。
# 批准 ExitPlanMode 后才进入实现

# 重构隔离 / 回滚 / 批处理
claude --worktree refactor-auth
# Esc 连按 或 /rewind 恢复对话与 Claude 改过的文件
claude -p   # 非交互批处理，适合已确认文件清单的机械迁移
```

- **坑与边界**：
  - Checkpoint 只覆盖 Claude 自己的改动与对话，不跟踪你在 IDE 里手改的内容，也不能替代 Git 提交点。
  - 页面明确点出的失败模式包括：在子目录启动、没有复现步骤就 Edit、一次改几十个文件、跳过计划直接写、只生成测试不跑测试、未验证就 push。
  - 测试输出与全量测试跑在主会话里是窗口膨胀的主因之一，页面在后一章建议改由子代理承接并只回摘要。
  - 本章从工作流 D 的实现段起（测试、Git/PR、文档、UI 调试、跨天并行、端到端示例、失败模式表）在抓取中被截断，仅按目录结构与已验证片段归纳（原文细节未完整获取）。
- **原文**：https://agent.jeffjade.com/claude-code/complete-workflow/

### 上下文管理与多代理架构

- **一句话**：长任务的失效不是报错而是"静默失真"——模型不会提醒你快满了，所以要把上下文管理当成架构设计，按 C（子代理）→ B（handoff）→ A（会话内补救）的优先级布防。
- **机制/要点**：
  - 代理循环每轮都把工具输出写回主会话，占用由 Read/Grep 片段、长 Bash 日志、多文件 diff、`CLAUDE.md` 与 MCP 工具定义的固定开销叠加而成。接近上限时模型不会停，症状是重复 Read 已看过的文件、忘记 `CLAUDE.md` 里的禁止项、偏离原计划仍自信改码，以及 autocompact thrashing（压缩后又被超大输出瞬间填满）。
  - 方案 A 是止血：`/context` 先看占用构成而不是猜，`/compact` 把同一任务的历史压短并可带聚焦指令，`/clear` 只清对话、`CLAUDE.md` 等记忆文件仍在。压缩只能减轻不能还原已经流失的注意力，所以连续 `/compact` 到 thrashing 是错误用法。
  - 方案 B 是把状态外化：自定义 `.claude/commands/handoff.md` 命令按固定结构写 `HANDOFF.md`（Status / Key Decisions / Files Modified / To Resume），再 `/clear` 开新会话；新会话能自动读它，靠的是 `CLAUDE.md` 里一条"若根目录存在 HANDOFF.md，做任何事之前先读它"的规则——而 `CLAUDE.md` 在 `/clear` 后仍会重新加载。触发时机有约定值：`/context` 到约 70% 时，完成当前小步后 handoff；即将 `/clear` 或关机前必须先 handoff。多窗口并行时改成分文件 `.claude/handoffs/YYYY-MM-DD_HH-MM_<id>.md` 并维护 `INDEX.md`，避免单文件被后写覆盖。
  - 方案 C 是结构上不爆：子代理在独立窗口承担重探索，返回主会话的只有摘要，主会话保持"指令 + 规划 + 摘要"三段小体积；内置 Explore 默认只读适合扫库，需要改码时用 general-purpose 或自定义子代理。边界同样清晰：子代理不能再派子代理，多层分工由主会话串联；摘要本身仍占 token；后台子代理遇到需要人工批准的操作会自动拒绝。
- **命令 / 配置 / 代码**：

```
# .claude/commands/handoff.md（自定义斜杠命令）
---
description: Write a session handoff file for cold resume
---
Create a HANDOFF.md file in the project root with this exact structure:
## Status
- Task: [what we were doing]
- Completed: [bullet list of done items]
- Next: [exact next step, specific enough to resume cold]
- Blocked: [anything unresolved]
## Key Decisions Made
## Files Modified   # 以 git status 为准
## To Resume
"Continue from HANDOFF.md. Read it first, then proceed with Next steps."

# CLAUDE.md 中的恢复规则（/clear 后仍生效）
## 会话恢复
如果项目根目录存在 HANDOFF.md，在做任何事之前先读它。

# 同一任务内瘦身，带聚焦指令
/compact Focus on: failing test auth.middleware.test.ts, last diff in src/middleware, open decision on session TTL
```

- **坑与边界**：
  - `HANDOFF.md` 含进行中状态，默认应进 `.gitignore`；只有当团队明确要共享交接单时才提交。
  - `Next` 写得模糊会让新会话继续跑偏，必须写成含文件路径与验收命令的可执行句；`Files Modified` 不核对 `git status` 就会与真实变更脱节。
  - 不要把 `/clear` 当 handoff 的替代：还有十几处未提交改动、或关键决定只存在于对话里（未写入文件或 Git）时，清窗等于丢决策。另需注意内置 `/handoff` 在页面写作时尚未作为官方命令发布，方案 B 只能用项目内自定义命令实现（原文明确标注该时间点）。
- **原文**：https://agent.jeffjade.com/claude-code/context-management/

### Claude Code 与生态的深度集成

- **一句话**：把"个人终端用顺手"扩到组织，关键是按本机 / 仓库 / 组织三层分别落地，并让仓库层成为可 review 的单一事实源。
- **机制/要点**：
  - 三层各有典型产物：本机是 IDE 扩展、终端 CLI、`~/.claude/settings.json`；仓库是 `.github/workflows/`、`.claude/`（skills、hooks、agents）、根目录 `CLAUDE.md`；组织是 managed settings、`permissions.deny`、`allowedMcpServers`、`allowManagedHooksOnly` 与用量看板。缺层时先补仓库层，再推组织层。
  - 记忆是分层的，且提交策略不同：仓库根 `CLAUDE.md` 入库供全员默认；`CLAUDE.local.md` 与 `settings.local.json` 不入库；monorepo 子目录 `CLAUDE.md` 按需加载；`.claude/skills/`、`hooks/`、`agents/` 建议入库以便 review。判断标准仍是"短、高频、可验证"，长流程进 Skills，硬约束进 Hooks 或 `permissions.deny`。
  - 权限模型的关键是 `deny` 优先于 `allow`：因此仓库级 `settings.json` 的 deny 列表等价于一条安全策略，合并前应与改 CI 同级 review。
  - 审计与拦截走 Hooks：`PreToolUse` 拦危险 Bash、把写操作日志送 SIEM；企业可开 `allowManagedHooksOnly` 只跑托管 Hook。开发者用 `/status` 能看到是否加载 Enterprise managed settings 及其来源（`remote`、`file`、`plist`）。
  - 交互式会话与无人值守流水线要分开设计：后者必须限制 `--allowedTools` 与 `--max-turns`，并禁止在无人工批准的情况下直推 main。
  - 推广按五阶段递进（试点 → 规范 → CI → 扩展 → 治理），度量看采用率、PR 周期、返工率与成本，而不是"生成了多少行代码"。
- **命令 / 配置 / 代码**：

```
// .claude/settings.json（仓库级，入库）
{
  "permissions": {
    "allow": ["Bash(npm run test *)", "Bash(npm run lint *)", "Read", "Grep", "Glob"],
    "deny":  ["Bash(git push *)", "Edit(.env)", "Edit(**/secrets/**)"]
  }
}
```

```
# 核查企业策略是否生效
/status        # 看 Enterprise managed settings 及来源 (remote)/(file)/(plist)
# 管理员策略下发路径之一
/etc/claude-code/managed-settings.json
```

- **坑与边界**：
  - 沙箱与 `deny WebFetch` 不是一回事：只要允许 Bash，`curl` 仍可能出站；敏感环境须启用沙箱的网络域白名单。
  - 仅持有 Bedrock/Vertex/Foundry 凭证时，Claude Code on the web、部分 Code Review、Routines 等功能可能不可用，需要额外席位（页面未给出具体价格或版本号）。
  - 团队规则被忽略的常见原因不是模型不听话，而是 `CLAUDE.md` 过长；关键项应改为 Hook 或 deny。
  - 明确列为越界的两件事：把生产密钥写进 `CLAUDE.md`、在无 review 的情况下自动合并到 main。
- **原文**：https://agent.jeffjade.com/claude-code/ecosystem-integration/

## 跨章节关联

- 与 [代理循环与工具](https://agent.jeffjade.com/claude-code/agent-loop/)：本册四章都建立在"工具输出每轮回写主会话"这一事实上；提示工程给循环定方向与停机判据，上下文管理解释循环为何会撑爆窗口。
- 与 [Plan Mode](https://agent.jeffjade.com/claude-code/plan-mode/)：工作流章的规划段、提示工程章的"探索/规划/实现"、上下文章的 Plan 子代理分工，是同一机制的三处引用；好计划的四要素（范围、步骤、决策、验收）是它们的共同接口。
- 与 [CLAUDE.md 编写与维护](https://agent.jeffjade.com/claude-code/claude-md/) 和 [团队记忆落地](https://agent.jeffjade.com/claude-code/memory-team-playbook/)：`/init` 后的人工删减、分层记忆的提交策略、`HANDOFF.md` 规则都落在记忆层，属于"跨会话持久约束"。
- 与 [SubAgents](https://agent.jeffjade.com/claude-code/subagents/) 和 [Agent Teams](https://agent.jeffjade.com/claude-code/agent-teams/)：上下文章的方案 C 是子代理的架构级用法（同章也点明子代理不可再派子代理）；Writer/Reviewer 双会话与 Agent Teams 属多会话协作的两个档位。
- 与 [Hooks](https://agent.jeffjade.com/claude-code/hooks/) 和 [Skills](https://agent.jeffjade.com/claude-code/skills/)：生态章把"长流程进 Skills、硬约束进 Hooks/deny"作为分层原则，`/handoff` 的实现则是自定义命令与 Skill 同名优先规则的实例。
- 与 [沙箱隔离机制](https://agent.jeffjade.com/claude-code/sandboxing/) 和 [安全边界与权限心智](https://agent.jeffjade.com/claude-code/security-permissions/)：生态章的 deny 规则、`curl` 出站问题、`allowManagedHooksOnly` 都是权限与沙箱两层模型的落地。
- 对应通用 Harness 概念：代理循环（轮次与停机条件）、工具（Read/Grep/Glob/Bash/Edit 的调用面）、权限（`--permission-mode`、allow/deny、`--allowedTools`）、上下文（窗口预算与压缩策略）、记忆（`CLAUDE.md`、`HANDOFF.md`）、Skill（`.claude/commands/`）、子代理（Explore/general-purpose）、MCP（`allowedMcpServers`）、沙箱（网络白名单与托管策略）。
- 后续章节衔接：[Agent SDK](https://agent.jeffjade.com/claude-code/agent-sdk/) 与 [CI/CD 与代码审查集成](https://agent.jeffjade.com/claude-code/ci-cd-integrations/) 把本册的 `claude -p`、`@claude` 用法做成流水线；[Token 成本与会话经济学](https://agent.jeffjade.com/claude-code/token-economics/) 接着算上下文账。

## 自测（3 题）

1. 为什么"让 Agent 能验证自己的工作"被页面称为单点最高杠杆？具体该把什么写进提示？
   提示：写出可执行命令与通过标准（相关测试全绿、截图对比一致、数字指标如覆盖率 62%→80%），代理循环才能自主迭代并自停，否则每错一次都要一次人工往返。
2. `/compact` 与 `HANDOFF.md` 都能"减负"，为什么前者替代不了后者？
   提示：`/compact` 只压缩同一会话的历史，丢掉的注意力细节无法还原；handoff 把状态、已做决定、下一步与改动清单外化成文件，能跨会话、跨天冷启动，并成为新会话的锚点。
3. 为什么仓库级 `.claude/settings.json` 的 deny 列表应与修改 CI workflow 同级 review？
   提示：`deny` 优先于 `allow`，它约束每台开发机上的工具调用（如 `git push`、`Edit(.env)`、`Edit(**/secrets/**)`），效果等同一条可被误改成放行的安全策略。
