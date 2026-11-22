# Claude Code · 第九部分 进阶实践（沙箱、安全、质量、组织）

> 本分册整理自智能体漫游（agent.jeffjade.com），覆盖章节：
> - 沙箱隔离机制 — https://agent.jeffjade.com/claude-code/sandboxing/
> - 安全边界与权限心智 — https://agent.jeffjade.com/claude-code/security-permissions/
> - 测试驱动协作与代码质量保障 — https://agent.jeffjade.com/claude-code/tdd-quality/
> - 团队与组织级落地 — https://agent.jeffjade.com/claude-code/team-organization/
> - 从工具使用到心智模型迁移 — https://agent.jeffjade.com/claude-code/mental-model-migration/

## 本册要点

- 权限规则与沙箱是**两层不同机制**：allow/deny 管「模型能不能调这个工具」，沙箱在操作系统层管「Bash 进程能不能碰这个路径/域名」；`deny WebFetch` 挡不住 `Bash(curl)`。
- deny 优先级最高且 bypass 也不能绕过，因此团队应把删除、密钥、生产配置、发布类命令写进 `permissions.deny`，再谈放行。
- 质量门禁的可靠顺序是「先写红测试 → 最小实现 → 只读审查」，并把格式化/测试挂到 `PostToolUse`/`Stop` Hook，因为 Hook 是确定性约束、CLAUDE.md 只是软提示。
- 组织落地靠分层作用域：managed settings 写不可协商的安全合规，项目 `CLAUDE.md` + `.claude/rules/` 写仓库事实，二者冲突时高层优先，用 `/status` 验证是否生效。
- 心智模型迁移的核心是把「搜索引擎式提问」换成「目标 + 验收 + 边界」的意图式提问，并把架构权衡、安全威胁模型、是否值得做留在人侧。

## 逐章笔记

### 沙箱隔离机制

- **一句话**：沙箱是在 OS 层给 Bash 执行环境套的笼子，决定进程能读写哪些路径、能连哪些网络，而不是决定模型能看见哪些工具。
- **机制/要点**：
  - 三道防线叠加：permission mode（是否弹窗）→ allow/deny（工具+参数模式匹配）→ 沙箱（进程级隔离），越往下越接近内核，越不依赖模型「听话」。
  - 之所以需要沙箱：一旦 Bash 被允许，模型完全可以写脚本绕过 Read 工具直接操作文件系统；Read/Grep 的路径限制对 shell 无约束力。
  - 沙箱包裹的是 **Bash 执行环境本体**，不是 Read/Grep 工具，所以它拦的是「进程行为」而非「工具调用意图」。
  - 环境选型按隔离强度分层：内置沙箱 Bash（中，日常防误删/限出站）< dev container（高，团队一致 + 不可信仓库）< Docker/VM（高，CI 与生产旁路）；裸机 + bypass 属最低强度，仅实验用。
  - 企业可通过 managed settings 强制开启沙箱，开发者用 `/status` 确认 `Enterprise managed settings` 是否真的加载——策略没下发时本地看着「已配置」其实是空转。
- **命令 / 配置 / 代码**：

```bash
# 交互式切换沙箱模式
/sandbox

# 验证沙箱是否生效（在非生产仓库执行，输出以实际环境为准）
touch /etc/claude-sandbox-test 2>&1 || true
curl -sI https://example.com 2>&1 | head -3

# 确认企业策略是否加载
/status
```

  - 配置入口为 `.claude/settings.json` 或 managed settings 中的沙箱相关项；具体键名以官方 Sandboxing 文档为准（原文未明确列出键名）。
- **坑与边界**：
  - `deny WebFetch` 不等于禁网：只要 Bash 可用，`curl`/`wget` 仍可出站，必须配网络域白名单。
  - 容器内路径映射与宿主机不同，测试在沙箱里莫名失败往往不是代码问题；应在 `CLAUDE.md` 写容器内命令。
  - 沙箱不是只读：项目目录仍可被删除，必须叠加 deny + Git 兜底。
  - 容器层有性能开销，不建议对所有任务常开，只在敏感任务或不可信输入场景启用。
- **原文**：https://agent.jeffjade.com/claude-code/sandboxing/

### 安全边界与权限心智

- **一句话**：权限系统回答的唯一问题是「哪类工具调用可以在没有人类确认的情况下执行」，你付的是便利，押的是可逆性。
- **机制/要点**：
  - 模式谱系：`default`（敏感操作弹窗）、`acceptEdits`（自动批准编辑）、`plan`（只读探索）、`bypassPermissions`（跳过几乎全部确认，仅限隔离环境）；另有 auto mode 等由组织策略配置的变体。
  - deny 的优先级高于 bypass：即使 `--dangerously-skip-permissions` 生效，`Bash(rm *)`、`Edit(.env*)` 之类规则仍会拦截；`rm -rf /`、`rm -rf ~` 这类根/家目录删除还有独立熔断提示。
  - 管理员可在 managed settings 用 `permissions.disableBypassPermissionsMode: "disable"` 从组织层禁用 bypass——这是防「个人图省事拖垮全队」的唯一硬手段。
  - 规则匹配的是**工具名 + 参数模式**，不是自然语言；因此「在对话里说不要改 .env」只是被分类器参考的软边界，compaction 之后可能丢失，硬保证必须落成 deny 规则。
  - allow/deny 与 Hook 的分工是确定性与表达力的权衡：静态禁止用 deny，预授权只读/测试用 allow，需要动态判断、审计、组织策略时用 `PreToolUse` Hook，且 Hook 在 allow 规则之前执行。
  - 作用域优先级：managed settings > 项目 `.claude/settings.json` > 用户 `~/.claude/settings.json` > `.claude/settings.local.json`（个人、不入库）。
- **命令 / 配置 / 代码**：

```json
{
  "permissions": {
    "deny": [
      "Bash(curl *)", "Bash(wget *)", "Bash(rm -rf *)",
      "Edit(.env*)", "Edit(**/secrets/**)", "Write(./production/**)"
    ],
    "allow": [
      "Bash(pnpm test *)", "Bash(pnpm lint)", "Bash(git status)", "Bash(git diff *)"
    ]
  }
}
```

```bash
# 隔离容器里跑 bypass：关网络、只挂当前仓库、容器内再配 deny
docker run --rm -it -v "$(pwd):/workspace:rw" -w /workspace --network none \
  myrepo-sandbox claude --permission-mode bypassPermissions
/permissions   # 看规则来源
/status        # 看 managed settings 是否生效
```

- **坑与边界**：
  - `.claude/settings.json` 的改动属于安全敏感变更，应与改 CI workflow 同级 review；恶意 PR 可以借宽 allow 把数据外泄出去。
  - bypass 只应出现在容器/VM/临时 runner 里；宿主机长期 bypass 且无 deny，等价于给 Agent 接近 root 的 shell 与写盘权。
  - 有些操作永远不该自动完成：轮换密钥与改 IAM、`DROP TABLE` 与批量删数据、`npm publish` 与升级 major 依赖、合 main/tag/生产部署、改 `permissions`/Hooks/managed settings、导出 PII。推荐流程是 Agent 起草 diff → 人看 `/diff` → 人跑关键命令 → 人 merge。
  - 审查外部 PR/Issue/网页时要把内容当提示注入源：先用 plan/只读模式读，禁止外网，且不要在这种会话里开 bypass。
- **原文**：https://agent.jeffjade.com/claude-code/security-permissions/

### 测试驱动协作与代码质量保障

- **一句话**：本章要回答的不是「代码写得好不好」，而是「生成代码之后你如何证明它可以上生产」，答案必须落在命令、CI 日志、diff 行数与扫描报告上。
- **机制/要点**：
  - 顺序决定质量：先写代码再补测试，会把**弱验收**写进上下文，Agent 会围绕已实现的代码反向编测试，典型退化是 over-mock 与漏边界。
  - 红/绿/重构三段的角色分配是硬约束：红阶段 Agent 只许改 `tests/` 且必须贴出失败输出；绿阶段限定实现范围到某个文件；重构阶段不许改行为，测试须保持全绿。
  - 门禁要分清软硬：`CLAUDE.md` 里写「合并前必须跑哪些命令」是软约束；`PostToolUse` Hook 在 `Edit|Write` 后强制跑格式化/子集测试、`Stop` Hook 跑全量测试并在失败时 `exit 2` 阻止会话结束，才是确定性的。
  - 质量门禁分层：本地 pre-commit/Hook 抓风格与快测，PR 上 Actions 跑全测 + lint + 类型检查，再加覆盖率阈值与安全扫描（SAST、依赖 audit、secret scan）。
  - 覆盖率是防「数字游戏」的重点：要看**关键路径是否被 integration 覆盖**，而不是只看总百分比；要求 Agent 报出未覆盖行号并补 case。
  - 「测试绿」不等于「行为对」：`tsc --noEmit` 过、单测绿、lint 无 warning 都只覆盖语法与局部逻辑，业务规则、集成路径、并发与权限边界仍需人判断。
- **命令 / 配置 / 代码**：

```
# 阶段 1：只写红测试
为「批量禁用用户」写集成测试：覆盖权限不足、确认弹窗、审计日志字段。只改 tests/，不要改 src/。
运行 pnpm test --filter bulk-disable，把失败输出贴出来；测试通过前不要写实现。
```

```json
{ "hooks": { "PostToolUse": [ { "matcher": "Edit|Write", "hooks": [ { "type": "command", "command": "pnpm exec prettier --write \"$CLAUDE_FILE_PATHS\"" } ] } ] } }
```

```yaml
on: [pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
      - run: pnpm install --frozen-lockfile
      - run: pnpm test && pnpm lint
```

- **坑与边界**：
  - 让 Agent「只跑一遍测试并总结」会得到叙事而非证据；必须要求贴原始输出与退出码。
  - 全量测试挂在 `Stop` Hook 上会明显拖慢每轮对话，应按 `src/` 路径 matcher 收窄或只跑子集。
  - 审查会话要给 diff 与测试输出，不给「我实现得多辛苦」的叙事，否则 reviewer 会被说服而不是被验证；实现与审查应分属两个会话。
  - 长 E2E 日志不要灌进主会话，交给子代理只回收 pass/fail，否则上下文被日志吃光。
- **原文**：https://agent.jeffjade.com/claude-code/tdd-quality/

### 团队与组织级落地

- **一句话**：把「每个人本地的 CLAUDE.md 都不一样」收敛成一套有归属、有评审、有维护节奏的分层约定。
- **机制/要点**：
  - 记忆与配置分多层作用域，冲突时高层优先：managed policy（IT 下发，macOS `/Library/Application Support/ClaudeCode/CLAUDE.md`、Linux `/etc/claude-code/CLAUDE.md`、Windows `C:\Program Files\ClaudeCode\CLAUDE.md`）> managed settings（远程/plist/注册表）> 项目 `CLAUDE.md` 与 `.claude/rules/` > `.claude/settings.json` > 用户级 `~/.claude/`。
  - 分工原则：managed 只写**不可协商**的安全与合规（禁 bypass、禁 MCP 清单、PII 与日志保留）；项目仓库写**本仓库事实**（构建命令、目录结构、模块约定、测试命令）。把团队全部细节塞进 managed 会导致更新慢、难 fork。
  - `CLAUDE.md` 需要维护节奏而非一次性生成：新人第一周被纠正的内容优先入库，PR review 中「Agent 本该知道」的点开 issue，季度做瘦身并迁移到 `paths` 规则或 Skills；必须指定 owner（模块 maintainer 轮值或平台组），避免「人人改、无人删」。
  - 可追溯性靠约定而非工具：commit body 标注辅助来源（`Assisted-by:` + 会话/计划信息），PR 模板里勾选 Agent 参与范围（仅人写 / Agent 起草人审 diff / Agent 跑测试人改架构）。
  - review 强度按文件类型分层：`src/**` 看逻辑与测试，`.claude/settings.json` 与 Hooks 等同改 CI，`CLAUDE.md` 看是否准确且不过长，lockfile 与 Dockerfile 看供应链。
  - 企业侧能力各有边界：Server-managed settings（无 MDM 也能下发）、Analytics（采用度看板）、ZDR（零留存，部分功能禁用）、网络（代理/自定义 CA/mTLS）、devcontainer、Bedrock/Vertex/Foundry；仅用云厂商凭证时，Web、Routines、部分 Code Review 可能不可用。
- **命令 / 配置 / 代码**：

```
## Claude Code 入职
1. 安装：见 docs/claude-code/installation-setup
2. 根目录 claude，运行 /doctor
3. 阅读 CLAUDE.md 与 .claude/rules/
4. 运行 /permissions 确认无意外 bypass
5. 练手：只读探索 issue #123 相关文件，再 /plan
```

```
feat(auth): bulk disable users

Assisted-by: Claude Code (session abc, plan approved 2026-03-15)
Tests: pnpm test --filter bulk-disable
```

- **坑与边界**：
  - 指标只应用于改进文档与 deny 规则，不应用于考核个人使用量，否则会诱导隐藏真实用法。
  - 对 Agent 生成的大 diff 要拆 PR 或分 commit，否则 review 形同虚设。
  - Issue/PR 评论触发 `@claude` 时，仓库 policy 必须写明禁止自动合 main、secrets 最小权限；无人值守不等于无人负责。
  - 共享 slash 命令（如 `/onboard-ticket`、`/ship-checklist`）放在 `.claude/commands/`，避免每人私藏一套流程。
- **原文**：https://agent.jeffjade.com/claude-code/team-organization/

### 从工具使用到心智模型迁移

- **一句话**：同一个工具，差别在提问范式——从「帮我找 X 在哪」的微观调度，转成「目标 + 验收 + 边界」的意图委派。
- **机制/要点**：
  - 搜索引擎式提问的失败信号是「你在做微观调度、模型没有完成判据」；意图式提问把成功标准外化为工作区里可验证的结果（测试全绿、文件被改对），失败时改的是目标与范围而不是换关键词。
  - 意图式不等于模糊：「尽量优化」仍然不合格，需要对象、动作、规则、异常、验收五元组齐全。
  - 放手与干预的决策树：先看风险高低 → 高风险走 Plan Mode 或人批计划；低风险但不熟域 → 先只读探索再计划；熟悉且低风险 → 交给多轮工具循环，运行中只监控是否偏离验收，偏离就暂停纠正或 `/rewind`。
  - 适合放手的场景共同特征是**失败成本低且可回滚**：机械改动、只读探索、已有 CLAUDE.md 与测试门禁的仓库。必须细粒度干预的是架构分叉、安全密钥合规、产品行为歧义、以及 Agent 连续两轮无进展。
  - 干预方式有优先级：**改验收/边界 > 口头否定 > 在长会话里堆补丁**；后者只会污染上下文并让模型误判目标。
  - 认知分工的判据是「判断权留人、材料收集可外包」：值不值得做、架构权衡、事故责任、威胁模型、对用户的承诺、何时停止投入留在人侧；语法样板、测试用例枚举、文档初稿、codemod 可外包。外包判断权的后果是代码能跑但没人能解释为什么这样设计。
- **命令 / 配置 / 代码**：

```
目标：修复 issue #42 描述的登出后 token 仍有效问题。
验收：pnpm test auth 全绿；不改 public API。
边界：只动 src/auth/ 与 tests/auth/；先 /plan 列出假设与文件清单。
```

```
# 四周练习计划的通过标准（摘）
第 1 周：每个任务先写验收 —— 提示里必含命令或测试名
第 2 周：大任务强制 /plan —— 无未经批准的大范围 Edit
第 3 周：实现与审查会话分离 —— 至少一次 reviewer 抓出问题
第 4 周：纠正写入 CLAUDE.md 或 rules —— 同类错误不重复第三次
```

- **坑与边界**：
  - 把「意图式」误解为「越抽象越好」，结果验收缺失，Agent 只能猜，产出不可 falsify。
  - 在长会话里反复口头否定而不改边界，等于用上下文噪声替代约束，越纠越偏。
  - 判断类工作外包后，PR review 被问住就是分工失败的信号；这属于组织风险而非效率问题。
  - Plan Mode 是「放手」与「干预」之间的握手契约，不是拖延流程，别用「先跑起来再说」跳过它。
- **原文**：https://agent.jeffjade.com/claude-code/mental-model-migration/

## 跨章节关联

- **与本站其他章节**：
  - 沙箱章与安全章是同一威胁模型的两面，前者管 OS 层、后者管工具层，二者都要叠在 [代理循环与工具](https://agent.jeffjade.com/claude-code/agent-loop/) 的 Bash 工具之上才有意义。
  - 权限的软硬边界依赖 [Hooks 机制](https://agent.jeffjade.com/claude-code/hooks/)，因为只有 `PreToolUse` 能在 allow 之前做动态判断与审计。
  - 质量章的「红测试先写」与 [Plan Mode](https://agent.jeffjade.com/claude-code/plan-mode/) 配合使用；审查环节复用 [提示工程秘诀](https://agent.jeffjade.com/claude-code/prompt-engineering/) 的 Writer/Reviewer 双会话。
  - 组织章是 [CLAUDE.md 编写与维护](https://agent.jeffjade.com/claude-code/claude-md-authoring/)、[团队记忆落地](https://agent.jeffjade.com/claude-code/memory-team-playbook/)、[团队 Skill 实战](https://agent.jeffjade.com/claude-code/skills-team-playbook/) 的上层收敛，并为 [生态深度集成](https://agent.jeffjade.com/claude-code/ecosystem-integration/) 里的 CI 触发设定边界。
  - 心智模型章是 [反思与进阶](https://agent.jeffjade.com/claude-code/reflection/) 的行为化版本，并与 [调试与错误恢复](https://agent.jeffjade.com/claude-code/debug-error-recovery/)、[Token 成本与会话经济学](https://agent.jeffjade.com/claude-code/token-economics/)、[上下文管理与多代理](https://agent.jeffjade.com/claude-code/context-management/) 互为补充。
- **与通用 Harness 概念的对应**：
  - **代理循环**：沙箱与权限都是循环里「工具调用 → 结果回注」这一步的前置闸门；放手的边界条件（失败成本低、可回滚）就是循环能否连续多轮的前提。
  - **工具**：allow/deny 的匹配单位是「工具名 + 参数模式」，这是 Harness 工具层的统一抽象；沙箱则是对 Bash 这一个工具的降级加固。
  - **权限**：权限模式谱系 + deny 优先级 + managed 强制，构成 Harness 的权力分层：用户在本地让渡多少权力、组织保留多少否决权。
  - **上下文 / 记忆**：CLAUDE.md 与 `.claude/rules/` 是可版本化的长期记忆，分层作用域是记忆的覆盖规则；报告「边界可能在 compaction 后丢失」则说明会话内约束不是记忆。
  - **Skill / 子代理 / MCP**：Skill 承接季度瘦身迁出的流程知识；子代理承接长 E2E 与只读探索以隔离上下文；MCP 的启用与限制则是 managed settings 的管控项之一。
  - **沙箱**：本册给出的完整谱系是「deny 模式 → 内置沙箱 Bash → dev container → Docker/VM → 临时 CI runner」，越往后越不依赖模型自律。

## 自测（3 题）

1. 已经 `deny WebFetch`，为什么 Agent 仍可能把仓库内容发到外网？该怎么补？
   - 提示：deny 只拦工具调用，Bash 里的 `curl`/`wget` 不受 WebFetch 规则约束；补网络域白名单或 `--network none`。
2. 团队要求「合并前必须跑测试」，写进 `CLAUDE.md` 和写成 Hook，差别在哪？各适合放什么？
   - 提示：CLAUDE.md 是软约束、可被忽略也可被 compaction 稀释；Hook 是确定性门禁，失败可 `exit 2` 阻断。风格/格式化与子集测试适合 Hook，流程说明与人读约定适合 CLAUDE.md。
3. 一个不熟的模块要做架构级改动，按本章的决策树你应该怎么走？
   - 提示：风险高 → 先 Plan Mode 或人批计划；不熟域 → 先只读探索再计划；执行中盯验收，偏离就改验收/边界（优先于口头否定）或 `/rewind`。架构权衡属于不可外包的判断。
