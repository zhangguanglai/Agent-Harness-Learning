# Claude Code · 第六部分 Skill 体系

> 本分册整理自智能体漫游（agent.jeffjade.com），覆盖章节：
> - 编码向社区 Skill 精选 —— https://agent.jeffjade.com/claude-code/skill-recommendations/
> - 社区技能目录导读 —— https://agent.jeffjade.com/claude-code/skill-catalog/
> - 团队 Skill 实战 —— https://agent.jeffjade.com/claude-code/skills-team-playbook/

## 本册要点

- 社区包的真正价值不是"多几个斜杠命令"，而是把工程纪律（先澄清 → 立计划 → 后改码）固化成一条可自动触发的技能链。
- 选型第一步是定归属：Plugin 市场跨项目跟版本、个人目录 `~/.claude/skills/` 只留一两个、项目仓库 `.claude/skills/` 可进 Git 与 code review。
- 装包等于给权限：第三方 Plugin 可声明 `allowed-tools` 扩大 Bash / Edit，star 数不能替代对清单的审查。
- 最大的隐性成本是上下文预算，技能描述被 `/doctor` 截断就等于触发词丢失，装了也不生效；收窄手段是 `skillOverrides`。
- 团队该沉淀的是自己的 `.claude/skills/` 而非社区包：社区项目是教材，有副作用的流程一律 `disable-model-invocation: true`，硬约束交给 Hook。

## 逐章笔记

### 编码向社区 Skill 精选

- **一句话**：把 Superpowers 这类方法论包理解为"接管任务节奏的流水线"，装之前先想清楚要的是流程、性格还是视觉规范。
- **机制/要点**：
  - 三种落地形态对应三种复用范围，选型顺序应是先定归属再谈装法：Plugin 市场（跨项目、跟版本）、个人目录（只取其中一两个技能）、项目仓库（团队统一、需 review）。
  - Superpowers 的主链是 brainstorming → using-git-worktrees → writing-plans → subagent-driven-development → test-driven-development → requesting-code-review → finishing-a-development-branch；每环有独立 `description`，靠描述匹配逐段触发，而不是一个巨型提示词。
  - 它自称 *Mandatory workflows, not suggestions*：技能一旦命中就把当前任务纳入固定流水线。这正是"装完变啰嗦、先问设计再写计划最后才动码"的成因——不是模型变慢，而是链上的前置技能在向你要输入。
  - Karpathy 准则属"性格层"而非"流程层"：Think Before Coding / Simplicity First / Surgical Changes / Goal-Driven Execution 分别治静默假设、过度抽象、顺手改无关文件、验收含糊。
  - 同一份准则两种成本模型：走 Plugin 是懒加载、按需触发；写进 `CLAUDE.md` 是启动全文注入，每次会话都付 token。
  - 三者维度正交——Superpowers 管工程流程，Karpathy 管克制程度，UI UX Pro Max 管视觉与设计系统（含 Design System Generator），可叠加不冲突。
- **命令 / 配置 / 代码**：

```
# Superpowers（官方市场）
/plugin install superpowers@claude-plugins-official

# 官方市场重名导致安装不稳时改走作者市场
/plugin marketplace add obra/superpowers-marketplace
/plugin install superpowers@superpowers-marketplace

# Karpathy 准则（Plugin 形态）
/plugin marketplace add forrestchang/andrej-karpathy-skills
/plugin install andrej-karpathy-skills@karpathy-skills

# UI UX Pro Max：Plugin 路径，或 CLI 安装（--global 写入 ~/.claude/skills/）
/plugin marketplace add nextlevelbuilder/ui-ux-pro-max-skill
/plugin install ui-ux-pro-max@ui-ux-pro-max-skill
npm install -g uipro-cli && uipro init --ai claude
```

- **坑与边界**：
  - 第三方 Plugin 可声明 `allowed-tools` 提升 Bash、Edit 权限，装包即授权；团队须对 `.claude/skills/` 与 Plugin 清单做 review。
  - 官方市场存在 Plugin 重名导致安装不稳定（obra/superpowers#355）；先用 `/plugins` 确认启用状态，再考虑换市场。
  - TDD 长文同时写进 `CLAUDE.md` 和 Superpowers 技能会双重注入、彼此打架：流程细节归技能，`CLAUDE.md` 只留不可推导的事实。
  - Plan Mode 与 Superpowers 不是替代关系：前者管"本轮能否改仓库"，后者管"按什么纪律推进"，混淆会误判为什么没生效。
- **原文**：https://agent.jeffjade.com/claude-code/skill-recommendations/

### 社区技能目录导读

- **一句话**：本章不列清单，只给一套"发现 → 评估 → 选型"的框架，因为真正的风险是恶意权限与上下文预算，而不是找不到包。
- **机制/要点**：
  - 站点明确声明只做分类与评估、不维护全量清单；官方也不为社区包背书。结论是选型责任在使用者，先看 Discover 的 **Will install**（会写入哪些文件）与 **Context cost**（描述占多少预算）。
  - 安装形态分三类，混用是最常见错误：Plugin 市场（`marketplace add` + `plugin install` 两步）、anthropics/skills 市场（`anthropic-agent-skills` 中的 `document-skills` / `example-skills`）、目录克隆型（gstack 克隆到 `~/.claude/skills/gstack` 后跑 `./setup`，**不走** `plugin install`）。
  - 四维评估框架：信任与透明（仓库可读、清单可核）、权限与治理（是否扩 Bash / Edit、允许哪些 scope）、上下文成本（描述预算）、重复与冲突（重名或流程重叠）。
  - 选型的判断依据是"缺流程还是缺检查单"：要角色化命令（`/review` `/qa` `/ship`）选 gstack；要默认先澄清再改码选 Superpowers；要压住过度设计选 Karpathy；要设计系统选 UI UX Pro Max。
  - 官方技能库（anthropics/skills）的正确定位是范本：学 SKILL.md 结构与 PDF / DOCX / PPTX 处理范式，而非找"最强技能"。
  - Skill 只能建议、不能保证执行；密钥扫描、禁路径这类安全诉求应交给 Hooks 的 `PreToolUse` 做确定性拦截，两者互补而非二选一。
- **命令 / 配置 / 代码**：

```
# 添加市场 → 安装，两步不可省
/plugin marketplace add <owner/repo>
/plugin install <plugin-name>@<marketplace-id>

# 官方技能库
/plugin marketplace add anthropics/skills
/plugin install document-skills@anthropic-agent-skills

# 目录克隆型（gstack）：不经 plugin 安装器
git clone https://github.com/garrytan/gstack ~/.claude/skills/gstack
cd ~/.claude/skills/gstack && ./setup

# 上下文预算体检
/doctor
```

- **坑与边界**：
  - 一次装十几个包会让 `/doctor` 里的技能描述被截断，被截断的描述等于触发词丢失，现象是"装了却不触发"。
  - 把 gstack 写成 `/plugin install`——它是目录克隆加 `./setup`，与市场安装不是一回事。
  - GitHub star 高不等于安全；决定它能对你仓库做什么的是 `allowed-tools` 字段，不是 star 数。
  - 只想借 Superpowers 的 `test-driven-development` + `systematic-debugging` 而不装全包，需用 `/skills` 或 `skillOverrides` 收窄可见性，否则主链仍会整体接管任务。
- **原文**：https://agent.jeffjade.com/claude-code/skill-catalog/

### 团队 Skill 实战

- **一句话**：社区包是教材不是长期依赖，团队要沉淀的是能进 Git、能被 code review 的 `.claude/skills/`，并知道何时改用 Hook。
- **机制/要点**：
  - 六条可迁移模式其实回答同一个问题：什么该拆、什么该藏、什么该禁、什么该分发。
  - 技能链优于单块长提示：把团队最长的粘贴提示拆成 2～4 个各带 `description` 的小技能，触发点更精确、失败可定位；`/do-everything` 式单技能无法说明坏在哪一步。
  - `description` 写"时机"不写口号：匹配发生在描述文本上，所以要写同事真实会说的话（「发 PR」「review diff」「上线 staging」）；"专业、高效"不构成触发词。
  - 副作用默认手动：部署、合并主干用 `disable-model-invocation: true`，或在正文首步强制人工确认环境——把"不会自动开火"写成声明而非叮嘱。
  - 长参考外置：规则进 `reference.md` 或 `scripts/`，`SKILL.md` 只写何时读哪个文件；单文件超过约 200 行就该拆，因为正文每轮都占上下文，附属文件按需读。
  - 流程稳定后才 Plugin 化（带版本号与可安装名单、review `allowed-tools`）；`writing-skills` 这类 Meta 技能把"如何写技能"本身标准化，团队应指定一人维护模板。
- **命令 / 配置 / 代码**：

```markdown
# .claude/skills/deploy-staging/SKILL.md
---
name: deploy-staging
description: 当同事说「上线 staging」「部署测试环境」「发到 staging」时使用；发布前核对版本与回滚方案。
disable-model-invocation: true
---

1. 向负责人确认目标环境与版本号，未确认则停止。
2. 跑 `pnpm build && pnpm test`，任一失败即中止。
3. 执行 `scripts/deploy.sh staging`，记录输出日志路径。
4. 详细检查项读 `reference.md`，不要内联到本文。
```

Skill 与 Hook 的分工：

- 硬性机械规则 → Hook：Edit 后必跑 formatter 用 `PostToolUse`，禁止 `rm -rf` 某路径用 `PreToolUse` exit 2（与模型记忆无关，不靠自觉）
- PR 审查 checklist → Skill（步骤多、需懒加载）
- 探索陌生模块并写报告 → SubAgent + Skill `context: fork`（隔离上下文）
- 每会话都得知道的测试命令 → `CLAUDE.md`（启动即加载）

- **坑与边界**：
  - 把社区包直接提交进生产仓库的 `.claude/skills/`；默认做法相反——先个人或试点项目验证，再以团队 Plugin 锁版本。
  - 用 Skill 承担本该 Hook 的职责（如"请不要删这个目录"），模型记不住就会踩；硬约束必须走 `PreToolUse`。
  - `description` 堆空泛形容词，队友用日常说法喊不动它，只能手动 `/skill-name`。
  - `SKILL.md` 膨胀到几百行，既吃上下文又降低遵守度；正文留步骤，规则外置。
- **原文**：https://agent.jeffjade.com/claude-code/skills-team-playbook/

## 跨章节关联

- **第五部分 · Skills 技能**：本册的上游，加载方式、`description` 写法、`context: fork`、`skillOverrides`、调试与失败模式均在该章，本册不重复。
- **第五部分 · Plugins 插件**：社区包几乎都以 Plugin 形态分发，marketplace 的创建与作者向发布流程见该章；本册只讲"装什么、怎么验"。
- **第五部分 · SubAgents**：`context: fork` 的技能实际在子代理中运行，用来把探索型任务的上下文开销隔离出去。
- **第五部分 · Hooks 机制**：Skill 与 Hook 的分工边界在本册第三章以表格给出——确定性拦截归 Hook，需懒加载的多步流程归 Skill。
- **第五部分 · MCP 协议**：MCP 提供工具与数据通道，Skill 提供过程知识；一个是"能做什么"，一个是"按什么步骤做"。
- **第四部分 · 项目记忆**：一句话事实进 `CLAUDE.md`（启动全文注入），多步流程进 Skill（懒加载），二者不要互相顶替。
- **第九部分 · 安全边界与权限 / 沙箱隔离机制**：`allowed-tools` 扩大的是权限面，须与权限心智、沙箱边界一起评估，不能只看功能描述。
- **第九部分 · 团队与组织级落地**：Plugin 锁版本、Managed settings、onboarding 只链 marketplace 名与版本，与该章的推广节奏对齐。
- **通用 Harness 概念映射**：Skill = 可懒加载的过程性上下文（区别于常驻记忆）；Plugin = 分发与权限声明的打包单元；`disable-model-invocation` = 代理循环中由人开门的闸门；Hook = 工具调用前后的确定性守卫；`context: fork` = 子代理的上下文隔离；`allowed-tools` = 权限面的静态扩张。

## 自测（3 题）

1. **同事抱怨"装完 Superpowers 后 Agent 变啰嗦，先问设计再写计划才动码"，这是缺陷还是设计？**
   提示：是主链设计。`brainstorming`、`writing-plans` 等前置技能命中后就会索要输入；若只要 TDD 纪律，用 `skillOverrides` 只保留 `test-driven-development` 与 `systematic-debugging`。
2. **gstack 与 Superpowers 的安装方式差在哪，为什么不能混用？**
   提示：Superpowers 走 `/plugin marketplace add` + `/plugin install`，受市场版本管理；gstack 是克隆到 `~/.claude/skills/gstack` 后跑 `./setup`，不经安装器——混用会得到一个"放好了但没注册"的目录。
3. **给"部署 staging"写技能，怎么保证模型不会自己开跑，且绕不过禁令？**
   提示：`disable-model-invocation: true` 关掉模型自动触发，只留人工 `/deploy-staging`；正文首步写人工确认；真正不允许的路径再叠 Hook `PreToolUse` exit 2 做硬拦截。
