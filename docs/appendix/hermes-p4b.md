# Hermes Agent · 第四部分 进阶（从零实现与自测）

> 本分册整理自智能体漫游（agent.jeffjade.com），覆盖章节：
> - 从零实现类似 Agent —— https://agent.jeffjade.com/hermes-agent/build-your-own-agent/
> - 贡献与社区 —— https://agent.jeffjade.com/hermes-agent/contributing-and-community/
> - 系列总结与自测 —— https://agent.jeffjade.com/hermes-agent/series-summary-and-self-test/

## 本册要点

- 自建 Agent 的正确顺序是「循环 → 持久化 → 工具 → 记忆 → Skill → 压缩 → Gateway → MCP → Profile」，
  跳过前四步直接做 Gateway 只会放大 bug。
- 九条设计原则里最容易被违反的是 **prompt 稳定性**（时间戳/动态记忆进 system）与
  **错误进对话**（工具异常炸穿循环）。
- 官方贡献优先级是 Bug > 跨平台 > 安全 > 健壮性 > Skill > Tool > 文档；**多数能力应做成 Skill 而不是 Tool**。
- Trajectory（ShareGPT JSONL）是训练数据来源，但「开 `save_trajectories` ≠ 微调完成」，
  还需要 Atropos 侧的环境、奖励与训练配置。
- 系列总结给出的六条「硬边界」（frozen snapshot、容器不自动 docker、YOLO 仍有 hardline 等）
  是最好的验收清单。

## 逐章笔记

### 从零实现类似 Agent

- **一句话**：用九个阶段把「工具型聊天机器人」逐步长成「有记忆、有技能、能多入口」的 Agent，
  每阶段都有明确交付物与验收标准，而不是一次性设计一个框架。
- **机制/要点**：
  - 目标画像：工具循环、持久会话、人格与事实文件（`SOUL.md`/`MEMORY.md`）、
    程序性记忆（`SKILL.md` + 渐进披露）、可选多入口。一句话心智模型是
    **「循环负责干活，文件夹负责变聪明」**。
  - 九条设计原则可当 checklist：平台无关核心、prompt 稳定、渐进披露、自注册、
    Profile 隔离、Agent 拥有学习产物、工具错误进对话、迭代预算、执行面分级。
  - 阶段 1 的最小循环只有 8 行：调模型 → 有 tool_calls 就执行并写回 → 否则返回文字。
    此步已具备「工具型聊天机器人」约八成价值。
  - 阶段 2-3：SQLite 存会话 + FTS5 供检索；工具注册表 + 五个起步工具
    （`read_file`/`write_file`/`list_dir`/`run_shell`/`web_fetch`），并用 toolset 控制暴露面。
  - 阶段 4-5：记忆三文件（SOUL/MEMORY/USER）注入 system 并**冻结快照**；
    Skill 走 L0 索引 / L1 `skill_view`，复杂任务（5+ 次工具调用）后鼓励 `skill_manage` 沉淀。
  - 阶段 6-9：Anthropic 的 `cache_control` 显式缓存；超过约 50% 窗口先剪旧工具输出再摘要；
    一个平台（如 Telegram）的 Gateway；stdio MCP；Profile 隔离 + `doctor` 自检。
  - 与 Hermes 仓库的对照表很有用：循环→`run_agent.py`、提示→`agent/prompt_builder.py`、
    工具→`tools/registry.py`、记忆→`memory_manager.py`、压缩→`context_compressor.py`。
- **命令 / 配置 / 代码**：

```python
# 阶段 1：最小循环（八行）
while True:
    response = call_llm(messages, tools=schemas)
    if response.tool_calls:
        for call in response.tool_calls:
            result = dispatch(call)          # 异常必须变成 result，不能抛出
            messages.append(tool_result(result))
        continue
    return response.text
```

```python
# 阶段 6：同会话内 system 必须字节稳定（可写进 CI 断言）
assert system_round_1 == system_round_10
```

- **坑与边界**：
  - 工具异常未捕获 → 整个会话崩溃；应对每个 handler `try/except` 并返回错误字符串。
  - system 里含每轮变化的时间戳 → 缓存与成本模型失效。
  - 无迭代上限 → 模型会 `list_dir` 数百次。
  - 生产仍用 `local` shell → 等同裸奔，需要容器或 SSH backend。
  - 测试污染真实 `~/.youragent` → 用环境变量 mock home。
  - 社区流传的「15 次工具调用才写 Skill」并非官方口径，官方表述是 **5+ 次**。

- **原文**：https://agent.jeffjade.com/hermes-agent/build-your-own-agent/

### 贡献与社区

- **一句话**：Hermes 的扩展哲学是「能用说明 + 现有工具表达的能力都做成 Skill」，
  Tool 只在需要运行时深度集成时才出现；新记忆后端则被明确要求做成独立插件而非合入主干。
- **机制/要点**：
  - 官方贡献优先级：Bug 修复 > 跨平台兼容 > 安全加固 > 性能健壮性 > 新 Skill > 新 Tool > 文档。
    大杂烩 PR 通常会被要求拆分。
  - Skill vs Tool 的判据：调用外部 CLI/HTTP、不需要 harness 内硬编码鉴权与流式的，
    一律优先 Skill（arXiv 检索、git 工作流、PDF 处理）；需要 API Key 管理、二进制/流式、
    必须每次精确执行的（浏览器会话、TTS、vision base64 管线）才做 Tool。
  - bundled 分层：`skills/` 给大多数用户、`optional-skills/` 给官方但非人人需要、
    Skills Hub / 自建 tap 给垂直与组织内部流程。
  - 新 `plugins/memory/` 后端**不再接受**合入核心树，应发布为独立插件并实现
    `MemoryProvider` ABC（`sync_turn`、`prefetch`、`shutdown`）；这是控制耦合与维护面的决定。
  - 核心 Skill 的审阅标准偏「可验证」：`description` ≤ 60 字符、引用原生工具名、
    含 When to Use / Procedure / Pitfalls / Verification、脚本放 `scripts/`、测试不访问真实网络。
  - 新增 Tool 的落地细节：`tools/` 新文件里 `registry.register(...)`，**同时**要在
    `toolsets.py` 把它加进列表，否则注册了也不会暴露给模型。
  - Trajectory：`agent.save_trajectories: true` 后会写 `trajectory_samples.jsonl`（完成）与
    `failed_trajectories.jsonl`（失败/中断），格式为 ShareGPT 兼容的
    `human`/`gpt`/`tool`/`system`；`batch_runner.py` 批量跑提示并产轨迹，无推理内容的样本会被丢弃。
- **命令 / 配置 / 代码**：

```bash
hermes skills browse
hermes skills install official/security/1password
hermes skills tap add myorg/hermes-skills
```

```yaml
# ~/.hermes/config.yaml
agent:
  save_trajectories: true   # 默认 false
```

- **坑与边界**：
  - 把小众内部流程硬塞进 bundled，会持续增加核心维护面；应走 tap。
  - 在 Gateway 聊天里提交密钥属于高危操作，应用 `hermes setup` 与 `.env`。
  - Trajectory 含完整对话，是隐私数据，不等于「合规手段」。
  - 不要以为贡献 Tool 比 Skill「高级」——官方明确 Tool 应少见。
  - 社区 Skill 安装会经 Skills Guard 扫描，`community` 来源可能需要显式 `--force`。

- **原文**：https://agent.jeffjade.com/hermes-agent/contributing-and-community/

### 系列总结与自测

- **一句话**：用一张 17 章能力地图 + 可勾选的自测清单，验收「是否真会用」，
  并明确 Hermes 与 Claude Code 的分工：Hermes 管长期驻留与多入口，Claude Code 管仓库内的工程循环。
- **机制/要点**：
  - 主轴一句话：模型无关的自主 Agent，通过 closed learning loop（`skill_manage` + `memory` +
    会话检索 + Curator）在长期运行中沉淀，经 CLI/TUI/Gateway/Cron 触达，
    用 toolsets 与执行 backend 约束破坏半径。
  - 相对普通聊天的三个可观察差异：工具循环、跨会话状态（MEMORY/USER/SessionDB+FTS5）、
    程序性记忆（SKILL.md 渐进披露）。
  - 六条硬边界：① frozen snapshot 不会立刻改变本会话 system；② 容器 ≠ 自动 docker，
    须显式 `terminal.backend: docker`；③ YOLO ≠ 无底线，hardline blocklist 仍拒绝灾难命令；
    ④ Skill 创建建议 5+ 工具调用后；⑤ Gateway 默认 deny，没有 allowlist 谁都用不了；
    ⑥ Trajectory ≠ 自动微调。
  - 「不必强上」清单：偶尔一问、深度改 monorepo（用 Claude Code）、严格禁止数据出网、
    期望零配置完美。
  - 最小验收实验（约 90 分钟）：`doctor` → 新会话让 Agent 写一条 memory →
    `--continue` 问「上次记住什么」对照 frozen 行为 → 完成多步任务看是否提议 `skill_manage`
    → `hermes tools` 记下 backend。
- **命令 / 配置 / 代码**：

```bash
hermes doctor && hermes model
hermes --continue           # 恢复同一会话
hermes tools                # 查看当前平台 toolset
```

- **坑与边界**：
  - 自测只勾概念不做操作，等于没学；清单每一项都要求「真跑一遍」。
  - 把 Hermes 与 Claude Code 混用同一 Profile / 密钥目录会互相污染，二者可共用
    `AGENTS.md` 风格的项目说明，但运行时目录要分开。
  - 教程有滞后性，版本行为以 `hermes --version` 与 release note 为准。

- **原文**：https://agent.jeffjade.com/hermes-agent/series-summary-and-self-test/

## 跨章节关联

- **与本册前文**：本册的「九阶段」是对 [hermes-p4a](hermes-p4a.md)（架构拆解）的动手版；
  架构拆解回答「代码长什么样」，从零实现回答「按什么顺序造」。
- **与 Claude Code 专题**：[cc-p9a](cc-p9a.md) 的 `/goal` 与 Routine 对应 Hermes 的 cron/长期驻留；
  [cc-p5](cc-p5.md) 的 SubAgents 对应 `delegate_task` 与 Kanban 看板。
- **与通用 Harness 概念**：
  - 循环 → [01 代理循环](../01-agent-loop.md)
  - 工具与 toolset → [02 工具系统](../02-tools-protocol.md)
  - 记忆冻结 → [04 上下文与记忆](../04-context-memory.md)
  - Skill 渐进披露 → [05 Skill](../05-skills.md)
  - 多入口与 Gateway → [07 扩展与多入口](../07-mcp-plugins-gateway.md)
  - 轨迹与成本 → [08 可观测与成本](../08-observability-cost.md)
- **与本仓库代码**：`mini_harness/loop.py` 就是阶段 1 的完整版；`memory.py` / `skills.py`
  是阶段 4-5；`trace.py` 是 Trajectory 的最小对应物（JSONL 事件流）。

## 自测（3 题）

1. **如果只能先做三件事，你会按什么顺序造 Agent？为什么不能先做 Gateway？**
   提示：循环不稳时，多入口只会把同一个 bug 复制到更多平台；顺序是循环 → 持久化 → 工具 →
   记忆 → Skill → 压缩 → 入口 → MCP → Profile。

2. **一个需求「每天把 GitHub issue 同步到飞书」，应该做成 Skill 还是 Tool？**
   提示：若能用 `terminal` + `web_fetch` + 现有 HTTP 工具表达，优先 Skill；
   只有需要长期会话、鉴权托管、精确二进制处理时才做 Tool。

3. **Hermes 的 frozen snapshot 与「让 Agent 记住偏好」是否矛盾？**
   提示：不矛盾。写入落盘、下一个会话生效；本会话内靠 tool result 告知最新状态。
   这是为了 prompt 前缀稳定与缓存可用，属于产品语义而非缺陷。
