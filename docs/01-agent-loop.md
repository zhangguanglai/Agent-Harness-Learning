# 01 · 代理循环：从「一次推理」到「一条任务链」

> 对应站点：[代理循环与工具](https://agent.jeffjade.com/claude-code/agent-loop/)、
> [Plan Mode](https://agent.jeffjade.com/claude-code/plan-mode/)、
> [架构拆解 · Agent Loop](https://agent.jeffjade.com/hermes-agent/architecture-deep-dive/)。
> 代码：[`mini_harness/loop.py`](../mini_harness/loop.py)、实验 [`lab01`](../labs/lab01_loop.py)。

## 1. 循环的最小形式

```python
while True:
    resp = llm.complete(messages, tools)      # 模型只能产出：文字 / 工具调用 / 两者
    if not resp.tool_calls:
        return resp.text                       # 出口一：自然结束
    for call in resp.tool_calls:
        result = execute(call)                 # 执行，异常也要变成结果
        messages.append(tool_message(result))
```

八行就是全部。真实产品在这八行外面加了四类东西：

| 加什么 | 为什么 | 本仓库位置 |
| --- | --- | --- |
| 停止条件 | 模型不会自己认输，必须有硬刹车 | `run()` 的预算/轮次检查 |
| 权限闸门 | 执行意味着真实副作用 | `_execute()` → `policy.decide()` |
| 上下文管理 | 多轮必然膨胀 | `_maybe_compact()` |
| 观测 | 否则无法归因 | `tracer.emit(...)` |

**判断一个 Agent 是否「循环跑偏」，先看它有没有明确的停止条件，而不是看模型聪不聪明。**

## 2. 「一轮」到底包含什么

一轮 = 模型调用 + 该轮所有工具执行 + 结果写回，**不含**下一次推理。

```
turn 1: [推理] → 调 Bash(npm test)
        [执行] → exit_code=1, 3 failed
        [写回] → 3 条 tool 消息
turn 2: [推理] → 读 auth.ts + 测试文件
        ...
turn N: [推理] → 纯文字：「修好了，测试全绿」
```

三个容易混的点：

1. **一轮可以有多个工具调用**。只读工具可并行，写操作必须串行（`ThreadPoolExecutor`
   只用于 `read_only` 工具，见 `Agent._execute`）。
2. **模型看不到自己的「执行结果」以外的任何东西**。它只知道工具返回了什么；
   你的终端里滚过的日志，如果不进 tool 消息，模型就不知道。
3. **最终回复只是出口**。中间轮次的文字往往是「思考痕迹」，不必逐条展示给用户。

## 3. 停止条件：五种出口，缺一不可

| 出口 | 触发 | 后果 | 站点对应 |
| --- | --- | --- | --- |
| 自然结束 | 末轮无工具调用 | 交付结论 | 官方 agent-loop |
| 轮次上限 | `turns >= max_turns` | 明确告知「未完成」 | `max_turns` |
| token 预算 | 累计 usage 超限 | 停止并保留现场 | `max_budget_usd` 同类 |
| 权限拒绝 | 连续两轮全部被拒 | 停，提示放宽或缩任务 | 权限拒绝 |
| 外部中断 | 用户 Esc / 信号 | 保留已完成的副作用 | 中断 |

本仓库的 `RunResult.stopped` 会精确记录是哪一个，实验 `lab01` 对四种出口都做了断言。
**生产环境最常见的坑是没有 2 和 3**：模型陷入「读文件 → 觉得不对 → 再读」的循环，
一晚上烧掉几百块。

## 4. 模型凭什么决定下一步

不要去猜模型的内部推理，观察输入侧和输出侧就够了。

**输入侧**（你给它的上下文）：

- 系统提示：身份、工具说明、记忆快照、技能索引、项目约定；
- 历史消息：之前轮次的文字与工具结果；
- 当前任务与约束。

**输出侧**（它每轮只能给三种）：

- 纯文字 → 循环结束；
- 工具调用 → 继续；
- 两者兼有 → 继续，文字是「边做边说」。

**你能施加影响的手段**，按有效性排序：

1. 把验收标准写成可运行的命令（「`pytest tests/` 全绿」比「修好 bug」强十倍）；
2. 用 `AGENTS.md` / `CLAUDE.md` 固定项目习惯（先跑哪个测试、目录约定）；
3. 用权限规则限制能力边界（不是限制「想不想」，而是「能不能」）；
4. 拒绝某次调用 —— 拒绝结果会回到对话里，模型通常会改道。

## 5. 工具的两种执行策略

站点里一句话，落到实现要小心：

> 只读类工具可并行；会改状态的工具顺序执行，避免冲突。

本仓库的做法（`Agent._execute`）：

```python
read_only_idx = [i for i, (d, c) in enumerate(prepared) if d.allowed and registry.is_read_only(c.name)]
serial_idx    = [i for i in range(len(calls)) if i not in set(read_only_idx)]

with ThreadPoolExecutor(max_workers=4) as pool:      # 只读并行
    ...
for i in serial_idx:                                  # 写操作串行
    results[i] = self._run_one(*prepared[i])
```

两个细节：

- **结果必须按原顺序写回**，否则模型看到 tool_call 与 tool_result 的顺序不一致；
- **不要并行执行需要人工审批的调用**，否则会同时弹出三个确认框。

## 6. Plan Mode：把「想」和「做」拆开

Plan Mode 的本质不是「给模型一个提示说先规划」，而是**在权限层把写工具抽掉**：

```python
# Agent._schemas()
names = registry.names(groups)
if policy.mode is Mode.PLAN:
    names = [n for n in names if registry.is_read_only(n)]
```

于是模型在物理上看不到 `write_file` / `bash` 的 schema，即使它硬编造一个调用，
`policy.decide()` 也会在第二道闸门拒绝。两层都要做：

| 只在提示词里说 | 只过滤 schema | 本仓库做法 |
| --- | --- | --- |
| 模型可能不听话 | 模型可能编造工具名 | schema + 执行层双保险 |

Plan Mode 的正确用法：

1. 只读探索：列目录、读关键文件、跑只读命令；
2. 产出计划：改哪些文件、怎么验证、风险点；
3. 人批准后切换到 `default` / `acceptEdits` 执行。

## 7. 失败模式对照表

| 症状 | 常见原因 | 先查什么 | 本仓库对应 |
| --- | --- | --- | --- |
| 一直读不改 | 目标含糊，或处在 plan 模式 | 验收标准；`policy.mode` | `lab02` 的 plan 断言 |
| Edit 反复失败 | 没读就改，或文件被外部改动 | `read_paths` 里有没有该文件 | `edit_file` 的前置检查 |
| 测试过了还继续 | 没有「完成」定义 | 把验收写成命令 | 系统提示第 5 条 |
| 突然变笨 | 上下文接近上限 | 轨迹里的 compaction 事件 | `lab03` |
| 子代理说做不到 | 子代理权限被收窄 | 子代理的 tool_groups | `lab06` |
| 批准了仍危险 | 规则过宽（`Bash(*)`） | 规则前缀粒度 | `policy.allow` |
| 一直弹窗 | 没写 allow 规则 | 高频命令加 `allow` | `lab02` |

## 8. 动手

1. 跑 `python labs/lab01_loop.py`，把 `max_turns` 改成 1，观察 `stopped` 变化。
2. 在 `lab01` 的场景 A 里，把剧本改成「同一轮发两个 `list_dir`」，在
   `_execute` 打一行日志，确认它们走了并行分支。
3. 用 `python -m mini_harness run "列出工作区文件" --mode plan --provider demo`
   观察模型拿到的工具 schema 里没有写工具（`--print-system` 可看提示）。
4. 回答：如果模型连续三轮都只调用被拒绝的工具，你的循环会在第几轮停？为什么是这个数？

## 9. 自测

1. 「一轮」和「一次会话」的边界在哪？
2. 为什么 `AGENTS.md` 里写「不要 push」仍可能 push？正确的做法是什么？
3. 只读工具并行执行时，最容易被忽略的正确性问题是什么？
4. Plan Mode 为什么要在 schema 和执行层各拦一次？
5. 你的 Agent 昨晚跑了 400 轮，今天怎么用轨迹定位它在哪一轮开始跑偏？
