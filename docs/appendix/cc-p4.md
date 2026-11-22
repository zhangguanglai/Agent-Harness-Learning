# Claude Code · 第四部分 项目记忆

> 本分册整理自智能体漫游（agent.jeffjade.com），覆盖章节：
> - 项目记忆总览 —— https://agent.jeffjade.com/claude-code/claude-md/
> - CLAUDE.md 编写与维护 —— https://agent.jeffjade.com/claude-code/claude-md-authoring/
> - 自动记忆与 /memory —— https://agent.jeffjade.com/claude-code/auto-memory/
> - Monorepo 与多工具记忆 —— https://agent.jeffjade.com/claude-code/memory-monorepo-ecosystem/
> - 团队记忆落地 —— https://agent.jeffjade.com/claude-code/memory-team-playbook/

## 本册要点

- 项目记忆只解决「上下文空窗口导致的重复交代」，它注入的是上下文而非强制配置；要「一定执行」必须换成 `permissions.deny` 或 Hook。
- `CLAUDE.md` 与 auto memory 是两条人/机分工的通道：前者进 Git 可 review、启动全文注入；后者由 Claude 写、存本机、只注入 `MEMORY.md` 前 200 行或 25KB。
- 目录树上的多个 `CLAUDE.md` 是**拼接**不是覆盖：向上 walk 启动加载，子目录懒加载（Read 时才注入），兄弟目录不加载。
- 单个 `CLAUDE.md` 超过约 200 行后遵守度与 token 成本双降，应拆到 `.claude/rules/*.md` 并用 `paths` 做文件类型作用域。
- 团队落地靠节奏而非一次性文档：`/init` 生成草稿 → 本地跑通命令 → PR 按五项清单 review → 每季度删过时条 → 晋升 auto memory 中的团队事实。

## 逐章笔记

### 项目记忆总览

- **一句话**：把团队已达成共识的硬事实写进每次会话都会加载的上下文，用「稳定注入」替代「反复纠正」，而不是指望模型变聪明。
- **机制/要点**：
  - 两套互补机制各有作者：`CLAUDE.md` 由人写、项目级可进 Git、用户级在本机；auto memory 由 Claude 自己写、按仓库哈希存本机、不进 Git。
  - 加载形态不同决定了写法不同：`CLAUDE.md` 是启动时**全文注入**，所以长度直接等于每轮固定开销；auto memory 只注入索引 `MEMORY.md` 的前 200 行或 25KB，细节留主题文件。
  - 加载规则是「沿目录树发现 + 拼接」，越靠近 `cwd` 的文件在拼接结果里越靠后，不存在「后加载覆盖先加载」。
  - 写入时机有明确信号：Claude 第二次犯同一个错、Code Review 指出「它本该知道」、上个会话打过的说明这次又要打一遍。
  - 记忆是引导层，权限与沙箱是强制层；两条线要分开设计，不能互相顶替。
  - 自动记忆存在版本门槛，页面标注需 v2.1.59+（以本机 `claude --version` 为准）。
- **命令 / 配置 / 代码**：

```markdown
# My Project
## 命令
- 构建: `pnpm build`
- 测试: `pnpm test`
## 硬约束
- 禁止修改 generated/
```

```bash
/memory   # 核对当前会话实际加载的 CLAUDE.md / CLAUDE.local.md / rules
```

- **坑与边界**：
  - 在 `CLAUDE.md` 写「禁止 force push / 禁止删文件」**挡不住**已授权的 `Bash`，安全边界只能落在 `permissions.deny` 或 Hook 上。
  - 把只属于某个子目录、或多步流程的内容全堆进根文件，会给每次会话加税，却换不来遵守度。
  - 记忆写不具体就守不住：「格式规范一点」这类形容词无法验证，「2 空格缩进」才是可执行事实。
  - 页面的通读时长、章节顺序属教程编排，不代表运行时的加载顺序。

- **原文**：https://agent.jeffjade.com/claude-code/claude-md/

### CLAUDE.md 编写与维护

- **一句话**：把 `CLAUDE.md` 当分层、可验证、可进 PR 的入职文档来维护，超过约 200 行就按作用域拆到 `.claude/rules/`。
- **机制/要点**：
  - 六层作用域从通用到局部：用户 `~/.claude/CLAUDE.md`、用户 `~/.claude/rules/*.md`、项目 `./CLAUDE.md` 或 `./.claude/CLAUDE.md`、项目 `.claude/rules/*.md`、本地 `./CLAUDE.local.md`、子目录 `<subdir>/CLAUDE.md`。前两者不入 Git，本地层应写进 `.gitignore`。
  - 用户级 rules 先于项目 rules 加载，页面明确「项目规则优先级更高」；具体冲突如何解析原文未明确，实践上应直接消除矛盾条目而非依赖优先级。
  - rules 分两种加载语义：无 frontmatter 的每次启动都加载；带 `paths` 的**仅在处理匹配文件时**加载。后者省的是每轮固定开销，不是「模型永远看不见」。
  - `paths` 支持 glob 与花括号（如 `src/**/*.{ts,tsx}`），rules 目录支持 symlink，便于多仓库共享同一份 `~/company-rules`。
  - `@path/to/file` 导入只是组织手段：相对路径相对当前文件、最多递归 5 层、首次外部导入要人工批准，被导入内容仍在启动时**全量进入上下文**。
  - `/init` 的价值是给出可验证草稿：已有文件时它建议改进而非覆盖；`CLAUDE_CODE_NEW_INIT=1` 会走交互式流程，可选生成 CLAUDE.md、Skills、Hooks。
  - 写作准则一句话：只写 Agent 无法从 `package.json` / `pyproject.toml` 可靠推断的条目（命令、硬约束、命名与目录、三句话架构、已知陷阱）。
- **命令 / 配置 / 代码**：

```markdown
# .claude/rules/api.md
---
paths:
  - "src/api/**/*.ts"
---
# API 规则
- 路由必须做参数校验
- 错误统一用 ApiError
```

```bash
# 从零建立基线：生成 → 本地验证 → 补硬约束 → 提交
/init
pnpm test
git add CLAUDE.md .claude/rules/
```

- **坑与边界**：
  - 用 `@` 导入不等于省 token，被 `@` 的文件照样启动时全量进上下文；想省开销只能用 `paths` 或干脆不写。
  - `CLAUDE.local.md` 忘记 gitignore 会把本机端口、内网地址、沙箱 URL 提交上去。
  - 多 worktree 时各 worktree 的 `CLAUDE.local.md` 不自动同步，跨 worktree 共享要写成 `@~/.claude/my-project-instructions.md`。
  - 团队并不遵守的规则、完整 API 文档、变更日志、能从目录树看出的结构都属「不必写」，写进去比留空更糟。
  - `/init` 会写出未经验证的命令，必须本地跑通后再提交。

- **原文**：https://agent.jeffjade.com/claude-code/claude-md-authoring/

### 自动记忆与 /memory

- **一句话**：auto memory 是 Claude 在会话中自行沉淀的跨会话偏好与经验，按仓库哈希落在本机，用 `/memory` 审计，团队级事实必须晋升进 `CLAUDE.md`。
- **机制/要点**：
  - 写入路径由措辞决定：对 Claude 说「记住」多数落到 auto memory；明确说「把这条写进 CLAUDE.md」才进项目文件。界面的 `Writing memory` / `Recalled memory` 就是这两个目录的读写指示。
  - 存储布局是「索引 + 主题文件」：`~/.claude/projects/<project-hash>/memory/MEMORY.md` 每次会话加载但有上限，`debugging.md` 等主题文件按需 `Read`。
  - `<project-hash>` 由 git 仓库推导，**同一仓库的 worktree 共享一份** auto memory；非 git 目录退回用项目根推导。
  - 索引超过 200 行后后半段不进启动上下文，所以细节必须下沉到主题文件，索引只留一行一条的指针。
  - 三级关闭方式：会话内 `/memory` 开关、项目 `.claude/settings.json` 的 `autoMemoryEnabled: false`、环境变量 `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`。
  - `autoMemoryDirectory` 只接受用户层或策略层配置，项目 settings 会被忽略（防止克隆别人的仓库把你的写入重定向走）；值须为绝对路径或以 `~/` 开头。
  - 排障顺序固定：先 `/memory` 看是否在加载列表 → 再核对 `cwd` 让 walk 拾取正确层级 → 再合并冲突条目 → 仍不满足就上 Hook。要看清「哪条说明何时加载」可用官方 `InstructionsLoaded` Hook 打日志。
  - `/compact` 后根 `CLAUDE.md` 从磁盘重新注入，子目录 `CLAUDE.md` 与只存在于聊天里的说明不会自己回来。
- **命令 / 配置 / 代码**：

```json
// .claude/settings.json（项目层）
{ "autoMemoryEnabled": false }
```

```json
// 用户层或 managed 层：重定向 auto memory 目录
{ "autoMemoryDirectory": "~/my-claude-memory" }
```

- **坑与边界**：
  - 团队共用的依赖（如「E2E 需要 `docker compose up -d redis`」）只在某个人本机的 auto memory 里，等于对团队不存在。
  - 改完 `CLAUDE.md` 当场不生效很常见：需要新开会话，或先确认它在 `/memory` 列表里。
  - 找不到某条细节通常不是丢了，而是它躺在主题文件里，`/memory` 打开目录全文看即可（全是明文 Markdown，可删可改）。
  - SubAgent 也能维护各自独立的 auto memory，但页面对其边界只给官方链接，细节原文未明确。

- **原文**：https://agent.jeffjade.com/claude-code/auto-memory/

### Monorepo 与多工具记忆

- **一句话**：monorepo 的记忆治理就是管住「祖先 walk 会捡什么」和「子包规则何时才加载」，顺带把其他 AI 工具的规则文件桥接进同一入口。
- **机制/要点**：
  - 拼接顺序有两条确定性规则：目录树上从靠近文件系统根到靠近 `cwd`（越近越靠后）；同一目录内先 `CLAUDE.md` 再 `CLAUDE.local.md`。
  - 三个方向三种行为：祖先方向在会话启动时全量加载；子目录方向在 Claude `Read` 该目录文件时才注入；兄弟目录**完全不加载**，所以在 `frontend/` 不会带上 `backend/CLAUDE.md`。
  - 噪声用 `claudeMdExcludes` 精确切除：配置在 `.claude/settings.local.json`，模式对**绝对路径**做 glob，user / project / local / managed 各层的数组会合并。
  - 托管策略 `CLAUDE.md` 设计上不可被排除，保证组织级说明在任何仓库都生效。
  - 分层写法：根文件只放全仓命令、共享 lint、禁止改动的目录；包内文件只写该包框架版本与差异约定，绝不复制根文件全文；按文件类型生效的规范交给 `.claude/rules/` + `paths`。
  - 配置边界要分清：Hooks、MCP 与部分 settings 仍以**仓库根**作为会话全局，只有技能与 `CLAUDE.md` 支持嵌套发现。
  - 跨工具桥接：`AGENTS.md` 与 `.cursor/rules` 都不会被自动加载，推荐单一事实源 + 在 `CLAUDE.md` 里 `@AGENTS.md`，Claude 专属补充另开 `## Claude Code` 小节。
- **命令 / 配置 / 代码**：

```json
// .claude/settings.local.json
{
  "claudeMdExcludes": [
    "**/monorepo/other-team/CLAUDE.md",
    "/absolute/path/to/noisy-rules/**"
  ]
}
```

```markdown
<!-- CLAUDE.md：把多工具说明收成一个入口 -->
@AGENTS.md
## Claude Code
对 `src/billing/` 下改动先使用 plan 模式。
```

- **坑与边界**：
  - 子包规则「没生效」的第一嫌疑是没读过该目录，记忆不读就不注入，光靠 `cd` 过去不够。
  - `/compact` 后子包约定会消失（根文件会重载），关键几条应上移到根或再次 `Read` 该目录。
  - 排除模式写相对路径无效，必须按绝对路径匹配；写错了不会报错，只会静默不生效。
  - 两套规则文件不同步会造成「Cursor 里对、Claude Code 里错」的行为割裂，需要季度对齐。

- **原文**：https://agent.jeffjade.com/claude-code/memory-monorepo-ecosystem/

### 团队记忆落地

- **一句话**：把前四章的机制固化成治理节奏——Managed 层写不可协商项、项目层走 PR review、auto memory 中的团队事实定期晋升进 Git。
- **机制/要点**：
  - 三层职责清晰分开：Managed policy 写安全与合规（位置随平台，见下方代码块）；项目 `CLAUDE.md` 写本仓库事实（构建命令、架构、陷阱）；用户级写个人偏好（语言、包管理器）。
  - Managed 还有一种等价形态：`managed-settings.json` 里的 `claudeMd` 字段，可与独立 Managed 文件二选一或并用。
  - 分工心智一句话：settings 管**强制拦截**（`permissions.deny`、沙箱），`CLAUDE.md` 管**行为引导**。把工程约定写进 `permissions.deny` 拦不住，把合规提醒写成 `CLAUDE.md` 也拦不住。
  - Managed 只放不可协商项；把团队全部细节塞进 Managed 会导致更新要走 IT 流程、别人难以 fork 复用。
  - 维护节奏：每个迭代把 review 里出现的「Agent 本该知道」开 issue 落成规则；每季度删过时条、合并冲突、检查是否超过 200 行需要拆分；PR 描述引用已批准的计划，并与 `CLAUDE.md` 里的验收命令对齐。
  - PR 记忆检查五项：是否可验证无模糊形容词、是否与 Managed 或现有 rules 冲突、是否该改成 `paths` 作用域、是否该交给 Hook（必须 deterministic 时）、auto memory 里的团队级事实是否已晋升进 Git。
  - 选型决策表：每会话都要知道的测试命令 → 项目 `CLAUDE.md`；Edit 后必须 format → `PostToolUse` Hook；偶尔才跑的发布清单 → Skill；禁止 `rm -rf` → `permissions.deny`。
- **命令 / 配置 / 代码**：

```json
// managed-settings.json
{ "claudeMd": "提交前运行 `make lint`。\n禁止直接 push 到 main。" }
```

```text
# Managed policy 文件位置
macOS        /Library/Application Support/ClaudeCode/CLAUDE.md
Linux / WSL  /etc/claude-code/CLAUDE.md
Windows      C:\Program Files\ClaudeCode\CLAUDE.md
```

```bash
git add CLAUDE.md .claude/rules/
git commit -m "docs: 更新 Agent 协作记忆（统一 pnpm test）"
```

- **坑与边界**：
  - 把仓库级事实写进 Managed 后，任何变更都要平台方发布，团队会绕开它，最终两套规则并存。
  - 必须每次执行的步骤（lint、format、禁 push）写成 `CLAUDE.md` 提醒只是「靠模型记得」，换成 Hook 或 deny 才可控。
  - 只在会话里纠正过、没进 Git 的记忆，对同事和 CI 都不可见，也会在下一次 `compact` 或换机器后消失。
  - 记忆文件不进 PR 评审就会熵增：规则越长越矛盾，遵守度反而下降。

- **原文**：https://agent.jeffjade.com/claude-code/memory-team-playbook/

## 跨章节关联

- **与本站前序章节**：[代理循环与工具](https://agent.jeffjade.com/claude-code/agent-loop/) 解释了记忆的注入位——它只是每轮被重读的上下文之一；[Plan Mode](https://agent.jeffjade.com/claude-code/plan-mode/) 是「大改前先规划」这条记忆条目的落地方式。
- **与本站后续章节**：[Hooks 机制](https://agent.jeffjade.com/claude-code/hooks/) 承接「必须 deterministic」的需求（`PostToolUse` 格式化、提交前 lint）；[Skills 技能](https://agent.jeffjade.com/claude-code/skills/) 承接多步流程与低频清单；[SubAgents](https://agent.jeffjade.com/claude-code/subagents/) 承接独立记忆作用域；[上下文管理与多代理](https://agent.jeffjade.com/claude-code/context-management/) 承接 `/compact`、200 行预算与长任务 handoff；[Token 成本与会话经济学](https://agent.jeffjade.com/claude-code/token-economics/) 解释 `paths` 省下的到底是什么。
- **与治理/安全章节**：[安全边界与权限心智](https://agent.jeffjade.com/claude-code/security-permissions/)、[沙箱隔离机制](https://agent.jeffjade.com/claude-code/sandboxing/)、[团队与组织级落地](https://agent.jeffjade.com/claude-code/team-organization/) 分别对应「deny 才是硬边界」「OS 层兜底」「Managed 部署细节」；[CLI 与配置查阅](https://agent.jeffjade.com/claude-code/cli-and-settings-reference/) 是 `autoMemoryEnabled`、`autoMemoryDirectory`、`claudeMdExcludes`、`claudeMd` 这些键的查表处。
- **与通用 Harness 概念的对应**：
  - 上下文 → 记忆是**注入源**，遵守「常驻内容必须短」的预算约束（200 行 / 25KB 两种上限是它的量化体现）。
  - 代理循环 → 每轮重新读取同一份上下文，所以记忆的收益是「每轮省一次纠正」，成本是「每轮付一次 token」。
  - 权限 / 沙箱 → 记忆的反面：能被说服的放记忆，必须被保证的放权限与 Hook。
  - 工具 → 记忆无法约束工具调用本身，只能影响模型选择哪个工具、传什么参数。
  - Skill / 子代理 → 按需加载的流程记忆与隔离的记忆作用域，与常驻记忆构成三层加载梯度（常驻 / 按路径 / 按需）。
  - MCP → 提供工具能力，不提供项目事实；连接了 MCP 不等于模型知道你的测试命令。

## 自测（3 题）

1. **`CLAUDE.md` 与 auto memory 分别由谁写、存在哪、启动时加载多少？**
   提示：人写 vs Claude 写；进 Git vs 本机 `~/.claude/projects/<hash>/memory/`；全文注入 vs `MEMORY.md` 前 200 行或 25KB。

2. **在 `backend/` 目录启动会话，`frontend/CLAUDE.md` 会被加载吗？`/compact` 之后子目录规则还在吗？**
   提示：兄弟目录不加载（三个方向：祖先启动加载、子目录 Read 时懒加载、兄弟不加载）；`/compact` 后只有根 `CLAUDE.md` 从磁盘重注入，子目录需再次 `Read`。

3. **某条规则「每次提交前必须跑 lint」，应该写在哪里？为什么不能只写 `CLAUDE.md`？**
   提示：写 Hook（或 `permissions.deny` 拦危险操作）。`CLAUDE.md` 只是上下文注入、无严格保证，属于行为引导而非强制拦截，还占每轮固定 token。
