# 05 · Skill：程序性记忆与渐进披露

> 对应站点：[Skills 技能](https://agent.jeffjade.com/claude-code/skills/)、
> [Skill 体系 · 社区精选](https://agent.jeffjade.com/claude-code/skill-recommendations/)、
> [技能系统实战（Hermes）](https://agent.jeffjade.com/hermes-agent/skills-in-practice/)、
> [记忆、学习与 Skill](https://agent.jeffjade.com/hermes-agent/memory-learning-skills/)。
> 代码：[`skills.py`](../mini_harness/skills.py)、实验 [`lab05`](../labs/lab05_skills.py)。

## 1. 三种知识，三种载体

| 知识类型 | 例子 | 载体 | 加载方式 |
| --- | --- | --- | --- |
| 事实性 | 「这个项目用 pnpm」 | `MEMORY.md` / `AGENTS.md` | 每次会话注入 |
| 程序性 | 「发版前怎么整理 CHANGELOG」 | `SKILL.md` | 按需加载 |
| 一次性 | 「这次改哪三个文件」 | 会话历史 / todo | 随会话消失 |

Skill 解决的是第二类：**步骤复杂的、可复用的、需要时可查的流程**。
把它写进系统提示会让前缀无限膨胀；让它每次重新摸索又浪费 token 和轮次。

## 2. 渐进披露：L0 / L1 / L2

```
L0（常驻 system）  名称 + 一句话描述             ~20-40 token/个
L1（按需）         skill_view(name) → 全文       几百 token，用到才付
L2（再按需）       SKILL.md 里引用的脚本/模板     用普通文件工具读
```

本仓库的实现：

```python
# system 里只放索引
"## 可用 Skill（L0 索引）\n" + manager.index() + "\n（任务相关时用 skill_view 读取全文）"
```

`SkillManager.index()` 只输出 `- **name**：description｜何时用：when` 一行。
实验 `lab05` 断言了「索引里没有正文标记，`skill_view` 之后才有」。

这个设计的杠杆率很高：**100 个 Skill 的索引可能只占 2k token，而全文可能是 200k。**

## 3. SKILL.md 的结构

```markdown
---
name: code-review
description: 按清单做一次结构化代码审查
when: 用户要求 review、检查 diff 或合并前自查时
---

## 步骤
1. 用 git status / git diff 列出改动范围
2. 逐个文件检查：错误处理、边界条件、命名、测试覆盖
3. 输出「必须改 / 建议改 / 可以忽略」三档，并给出文件:行号

## 参考
- 测试命令：`pnpm test:unit`
- 如需模板，读取 `templates/review.md`
```

三条写作经验：

1. **description 决定「会不会被加载」**，要写触发场景，而不是功能概述；
2. **步骤要可执行**，最好带命令；
3. **重内容放 L2**，SKILL.md 只留骨架和引用路径。

## 4. Skill 从哪来：自沉淀闭环

Claude Code 与 Hermes 都强调「Agent 自己写 Skill」。闭环是：

```
完成一个非平凡任务
      ↓
判断是否值得复用（站点口径：用掉 5+ 次工具调用）
      ↓
skill_manage(create) 写入步骤
      ↓
下一个会话的索引里出现它
      ↓
下次遇到同类任务，先 skill_view 再执行
```

本仓库 `skill_manage` 的 description 里写明了这个建议，`lab05` 演示了完整一轮。

**注意「下一个会话才生效」与记忆是同一条冻结语义**：新 Skill 的索引不会
中途插进当前 system（否则破坏前缀缓存）。

## 5. 什么时候不该写 Skill

- 一次性任务（写完就过期）；
- 更适合做成工具的事（确定性逻辑应该写代码，不该让模型按步骤执行）；
- 依赖大量隐式上下文、无法用文字复现的事。

一个判据：**如果把这段步骤交给一个新同事，他照着能做对吗？** 不能，就别写 Skill。

## 6. Skill 的治理（团队场景）

| 问题 | 做法 |
| --- | --- |
| 重复 Skill 泛滥 | Curator 定期合并/归档（Hermes 有 `hermes curator`） |
| 质量参差 | Code review 流程审 `SKILL.md`，和代码同等对待 |
| 引用不存在的工具 | 写 frontmatter `requires_tools`，安装时校验 |
| 版本漂移 | Skill 跟仓库走，进 git，走 PR |
| 安全 | SKILL.md 是「给模型看的指令」，等同于代码执行权限，必须 review |

最后一条最容易被忽略：一个恶意 Skill 可以指示 Agent 执行任意操作，
所以 **Skill 库的信任边界等同于代码库**。

## 7. Skill vs 工具 vs 提示词

| | 提示词 | Skill | 工具 |
| --- | --- | --- | --- |
| 形态 | 文字 | Markdown 文档 | 代码 |
| 执行者 | 模型 | 模型 | 运行时 |
| 确定性 | 低 | 中（有步骤约束） | 高 |
| 适合 | 风格、语气、约束 | 流程、检查清单 | 有明确输入输出的操作 |
| 加载 | 常驻 | 按需 | schema 常驻 |

判断顺序：**能写成工具的，不要写成 Skill；能写成 Skill 的，不要塞进系统提示。**

## 8. 自测

1. 为什么 Skill 要分 L0/L1 两层？如果全部常驻 system 会怎样？
2. `description` 和 `when` 哪个更影响「是否被加载」？为什么？
3. 什么情况下应该把一段流程做成工具，而不是 Skill？
4. 「Agent 自己写 Skill」的风险是什么？团队该怎么治理？
5. 新 Skill 为什么不在当前会话的索引里生效？这和文档 04 的哪条原则是同一件事？
