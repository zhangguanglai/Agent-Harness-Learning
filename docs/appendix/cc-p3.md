# Claude Code · 第三部分 工作原理

> 本分册整理自智能体漫游（agent.jeffjade.com），覆盖章节：
> - Claude Code 的工作原理：代理循环与工具调用 —— https://agent.jeffjade.com/claude-code/agent-loop/
> - Plan Mode：先规划再执行 —— https://agent.jeffjade.com/claude-code/plan-mode/

## 本册要点

- 「一轮」= 一次带工具调用的模型回复 + 工具执行 + 结果回写上下文；循环在模型某轮不再要工具时自然终止，而不是靠外部判定。
- 工具名就是权限规则的字符串键：`Read`/`Glob`/`Grep` 默认免批，`Edit`/`Write`/`Bash` 默认弹窗，规则写成 `Tool(specifier)` 做精确限权。
- 规则评估顺序固定为 deny → ask → allow，先命中先生效；`Bash(cmd1 && cmd2)` 会按 `&&` 拆段分别匹配。
- 提示词与 `CLAUDE.md` 管「模型想做什么」，permissions / 沙箱 / Hooks 管「允不允许做」——前者无法替代后者。
- Plan Mode 的价值不是「多问一次」，而是强制产出一个可审查的中间态：要改的文件清单 + 验收命令 + 执行模式选择。

## 逐章笔记

### Claude Code 的工作原理：代理循环与工具调用

- **一句话**：模型在每一轮自行判断是否需要工具，运行时执行工具并把真实输出写回上下文，这个「观察→行动→读结果」的闭环一直转到某一轮只产出文字为止。
- **机制/要点**：
  - 循环的输入侧是四类东西：对话历史、`CLAUDE.md` 等常驻说明、工作区结构与会话内已读内容、以及各轮工具返回（测试日志、grep 结果、编辑成功/失败）。模型不是「记得」文件，而是这些结果仍在上下文里。
  - 输出侧只有三种形态：纯文字、一个或多个工具调用、或两者并存。终止条件是「本轮没有工具调用」，这解释了为什么同样一句话背后可能跑了几十轮。
  - 工具是具名的环境操作通道，同一个字符串同时被权限规则、Hooks、子代理配置引用；`Read`/`Glob`/`Grep` 免批但**照样吃 token**，大仓库广撒网搜索是隐形成本。
  - 只读类工具可并行，改状态的工具串行执行以避免写冲突——终端里短时间连出多个 `Read` 属正常调度，不是失控。
  - `Edit` 依赖 `old_string` 精确匹配，且要求文件已被读过、磁盘未变；匹配失败时模型会加长片段或重新 `Read`，所以「同一文件反复改不对」通常是外部并行修改导致的。
  - 权限之外还有独立防线：沙箱在 OS 层限制 `Bash` 的路径与网络；Hook 的检查时机早于 allow 规则；后台子代理不会弹权限窗，只能沿用父会话已授予的权限，未批准调用直接拒绝。
- **命令 / 配置 / 代码**：

```json
{
  "permissions": {
    "allow": ["Bash(npm run test *)", "Bash(git status *)", "Read"],
    "deny":  ["Bash(git push *)", "Read(./.env)"],
    "ask":   ["Bash(curl *)"]
  }
}
```

```bash
# 会话内管理权限与后台任务
/permissions        # 增删 allow / deny / ask 规则
/tasks              # 查看、停止 run_in_background 起的进程
/context            # 检查上下文占用
```

- **坑与边界**：
  - 在 `CLAUDE.md` 写「禁止 push / 禁止删库」不构成强制约束，必须落成 `deny` 规则；模型可以「不想」，但只有权限能「不让」。
  - 允许 `Bash` 后，`curl` 可访问任意 URL，不受 `WebFetch(domain:...)` 的域名白名单限制；只查文档时应 deny 通用 curl。
  - 默认单条 `Bash` 约两分钟超时，dev server、watch 测试这类长进程必须显式放后台或拆命令，否则会被判超时。
  - `bypassPermissions` 仅适合隔离容器，仍会拦截删根目录级别的极端命令，且不应在存放密钥的本机长期开启。

- **原文**：https://agent.jeffjade.com/claude-code/agent-loop/

### Plan Mode：先规划再执行

- **一句话**：在同一个代理循环前面插入一个不改源码的只读规划段，用「批准计划」这个人工检查点把探索上下文和执行上下文切开。
- **机制/要点**：
  - 「灾难循环」是可观测的症状集合而非修辞：修 A 坏 B、同一文件反复猜 `old_string`、测试绿了还在动、diff 越来越大却说不清目标。根因是缺少可验证的中间态——动手前双方没对齐「改哪些文件、什么顺序、怎样算完成」。
  - 必须区分两层概念。`plan` 是权限模式：禁止编辑源码文件，但 `Read`/`Grep`/`Glob` 照常可用，`Bash` 仍按 `default` 的规则申请批准——所以 plan 模式下**照样会弹 Bash 弹窗**。`EnterPlanMode`/`ExitPlanMode` 是工具：前者进流程无需批准，后者提交计划、请求批准。
  - 计划应读起来像一份可执行的 PR 描述，审查时核对四类信息：范围（动哪些路径、哪些明确不动）、步骤（顺序与依赖）、决策（方案 A/B 选谁及理由）、验收（跑哪条命令算完成）。
  - 批准对话框同时是「执行模式选择器」：可进入自动执行、`acceptEdits`、逐条审查编辑，或退回继续规划；`Ctrl+G` 可在批准前用系统编辑器直接改计划文本。
  - 批准后仍走代理循环，只是多了任务项跟踪（交互会话用 `TaskCreate`/`TaskList`/`TaskUpdate`，`claude -p` 与 Agent SDK 场景仍常见 `TodoWrite`）。
  - 它不能替代代码审查：若批准后选了宽松模式，模型依旧可能执行宽泛的 `Bash`，长期防线还得靠 `deny`、沙箱与 Hooks。
- **命令 / 配置 / 代码**：

```bash
# 启动即进入 plan 模式
claude --permission-mode plan

# 单条消息触发规划
/plan 把 auth 登录从 session 迁到 JWT，先不要改任何文件
```

```json
{
  "permissions": { "defaultMode": "plan" }
}
```

```text
Shift+Tab    # default → acceptEdits → plan 循环切换；再按一次退出规划且无需批准计划
Ctrl+G       # 批准前用编辑器修订计划正文
```

- **坑与边界**：
  - 计划写得虚（「我会优化代码」）等于没有检查点；提示里应强制要求「必须包含文件列表与验收命令」。
  - 模型可能自行调用 `EnterPlanMode`，若希望始终由自己触发，可在提示中声明，或 `deny` 该工具——代价是失去自动建议规划的能力。
  - 执行偏离时不要补丁叠补丁：停掉大范围 `Edit`，重新 `/plan` 或声明「第 N 步假设不成立，请修订计划」，比在同一上下文硬扛更省 token。
  - 规划段残留的错误假设会污染执行段，可启用 `showClearContextOnPlanAccept` 在批准时清空规划上下文，或 `/clear` 后只带计划执行；接受计划后会话名会按计划内容自动命名，除非已用 `--name`/`/rename` 指定。（另注口径差异：本章称 `plan` 为「六种权限模式之一」，而代理循环章仅列五种 `default`/`acceptEdits`/`plan`/`dontAsk`/`bypassPermissions`，第六种原文未明确。）

- **原文**：https://agent.jeffjade.com/claude-code/plan-mode/

## 跨章节关联

- **与本站其他章节**：
  - 前接 [多平台运行环境全览](https://agent.jeffjade.com/claude-code/platforms-overview/)、[第一个会话](https://agent.jeffjade.com/claude-code/first-session/) 与 [Slash 命令](https://agent.jeffjade.com/claude-code/slash-commands/)——`@` 引用与 `/compact` 正是控制只读工具上下文成本的工具；`/permissions`、`/tasks`、`/plan` 都属于 Slash 命令面。
  - 后接 [项目记忆总览](https://agent.jeffjade.com/claude-code/claude-md/)：`CLAUDE.md` 决定循环里的默认习惯（先跑哪条测试），但不具备强制力，与 `deny` 规则互补。
  - 深化见 [Hooks 机制](https://agent.jeffjade.com/claude-code/hooks/)（Hook 先于 allow 规则评估）、[SubAgents](https://agent.jeffjade.com/claude-code/subagents/)（后台子代理不再弹窗）、[沙箱隔离机制](https://agent.jeffjade.com/claude-code/sandboxing/)（OS 层兜底）、[安全边界与权限心智](https://agent.jeffjade.com/claude-code/security-permissions/)、[上下文管理与多代理](https://agent.jeffjade.com/claude-code/context-management/)、[/goal 与跨轮持续目标](https://agent.jeffjade.com/claude-code/goal-mode/)（用可验证终点解决「停不下来」）、[调试与错误恢复](https://agent.jeffjade.com/claude-code/debug-error-recovery/)（失败模式排查）、[Agent SDK 程序化调用](https://agent.jeffjade.com/claude-code/agent-sdk/)（`max_turns`/`max_budget_usd`）。
  - 横向对应 [MCP 协议](https://agent.jeffjade.com/claude-code/mcp/) 与 [Skills 技能](https://agent.jeffjade.com/claude-code/skills/)：MCP 工具、`Skill` 工具、`EnterPlanMode` 与内置工具共用同一套具名工具与权限字符串体系。
- **与通用 Harness 概念的映射**：
  - 代理循环：本册第一章即标准 ReAct/工具循环，关键在「终止条件 = 无工具调用」与轮次定义。
  - 工具：具名工具即能力边界，「只读可并行、写操作串行」是可移植的调度约束。
  - 权限：三层（只读 / 改文件 / 执行）+ 三档规则（allow/deny/ask）+ 固定评估顺序，是 Harness 权限模型的最小完备集。
  - 上下文：「只读也耗 token」「探索与执行混在同一上下文」是上下文工程的两个核心痛点，对应 `/compact`、`/clear` 与批准时清空。
  - 记忆：`CLAUDE.md` 是软约束的持久记忆，与硬约束的权限分离。
  - Skill / 子代理：子代理隔离上下文、只回报一行，代价是父会话失去可见性与权限再申请能力。
  - 沙箱：与权限叠加的 OS 层约束，处理「规则写漏了」的兜底。
  - MCP：外部工具接入后，工具名与权限规则键同步扩展。
- **两章之间的接力关系**：第一章把循环定义为「模型自主决定、权限决定能否执行」，第二章在同一循环外加一个前置只读段，把「想」与「做」拆开。二者不是两套机制，Plan Mode 只是把权限模式收敛到 `plan` 并叠加 `ExitPlanMode` 这个提交点；理解这一点，就不会把 Plan Mode 误当成独立工作流。
- **检查点的三个位置**：批准计划时（人工、一次性）、Hook 执行时（脚本化、每次工具调用）、沙箱运行时（内核级、无提示）。层次从「对齐意图」到「强制隔离」逐级变硬，缺任何一级都会留下缝。
- **成本与稳定性取舍**：只读探索省的是误改风险，花的是 token；把验收写成可运行命令省的是反复试错，花的是前期想清楚的时间。这一点与 [Token 成本与会话经济学](https://agent.jeffjade.com/claude-code/token-economics/)、[测试驱动与质量保障](https://agent.jeffjade.com/claude-code/tdd-quality/) 直接相关。

## 自测（3 题）

1. 「一轮」和「一整次会话」差在哪？为什么这个区分会影响你排查「它为什么一直在读文件」？
   - 提示：一轮 = 一次带工具调用的模型回复 + 工具执行 + 结果回写；一次会话含多轮。一直读说明模型尚未拿到足以收敛的证据，或停止条件（验收命令）没写清。
2. 为什么在 `CLAUDE.md` 里写「不要执行 git push」仍可能 push，而 `Bash(git push *)` 放在 `deny` 里就不会？
   - 提示：前者进的是模型输入，只影响意图；后者进的是运行时评估链，deny 先于 ask/allow 命中，工具根本无法执行。另注意 `&&` 复合命令会逐段匹配。
3. `plan` 权限模式与 `ExitPlanMode` 工具各解决什么问题？为什么 plan 模式下仍会弹 Bash 权限窗？
   - 提示：`plan` 是模式，禁的是「编辑业务源码」，不豁免 `Bash` 的批准规则；`ExitPlanMode` 是提交计划并申请批准的工具，需你点头才离开规划段。前者管「能改什么」，后者管「何时可以开始改」。
