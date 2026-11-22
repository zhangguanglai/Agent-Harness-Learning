# 04 · 上下文与记忆：让 Agent 不变笨、不忘事

> 对应站点：[上下文管理与多代理](https://agent.jeffjade.com/claude-code/context-management/)、
> [项目记忆总览](https://agent.jeffjade.com/claude-code/claude-md/)、
> [CLAUDE.md 编写与维护](https://agent.jeffjade.com/claude-code/claude-md-authoring/)、
> [记忆、学习与 Skill（Hermes）](https://agent.jeffjade.com/hermes-agent/memory-learning-skills/)。
> 代码：[`context.py`](../mini_harness/context.py)、[`memory.py`](../mini_harness/memory.py)、
> 实验 [`lab03`](../labs/lab03_context.py)、[`lab04`](../labs/lab04_memory.py)。

## 1. 上下文是一种会溢出的资源

一次会话的上下文里同时住着：系统提示、项目约定、记忆、技能索引、历史对话、工具结果。
四条经验法则：

1. **前缀稳定** → 缓存命中、成本下降；
2. **结果裁剪** → 单条工具输出有上限；
3. **历史折叠** → 接近上限时压缩中段；
4. **按需加载** → 技能/文件不要一次性全塞进去。

这四件事的共同点：都是**工程策略**，不是模型能力。

## 2. Prompt 稳定性：为什么时间戳是成本杀手

KV 缓存按前缀匹配。系统提示里每轮变一个字符，整段前缀就失效，输入 token 全额重算。

```python
# 只在 run() 开始时构建一次
self.system_prompt = self._build_system(task)
```

本仓库刻意**不**把「当前时间」「轮次号」写进系统提示；`Tracer` 会记录
`system_hash`，实验可以断言同一会话内它不变。

会破坏稳定性的常见写法：

| 写法 | 后果 | 正确做法 |
| --- | --- | --- |
| system 里拼 `datetime.now()` | 缓存全失效 | 放到 user 消息或工具结果里 |
| 每轮刷新 `MEMORY.md` 进 system | 成本 × 轮数 | 冻结快照（下节） |
| 中途切 toolset | 前缀变了 | 下一个会话生效 |
| 每轮重排工具 schema | 同上 | 启动时定型 |

## 3. 冻结快照：记忆是「上一轮会话」的产物

Hermes 的产品决策值得抄：会话开始时读取 `SOUL.md` / `MEMORY.md` / `USER.md`，
拼进 system 后**冻结**；会话中 `memory` 工具可以写盘，但已发送的 system 不变。

```python
def freeze(self) -> dict[str, str]:
    self.snapshot = {kind: read_text(self._path(kind)) for kind in KIND_LABEL}
    return self.snapshot
```

好处：

- prompt 前缀字节稳定，缓存可用；
- 「本会话刚记住的」与「提示里已知的」可区分，调试时不会自欺；
- 写入失败/膨胀不会立刻污染当前会话。

代价：

- 新记忆要下一个会话才生效；
- 用户说「记住」后立刻问「我刚才说了什么」，模型可能依赖 tool result 而非 system。

实验 `lab04` 把这两条都断言了。这不是 bug，是**语义**。

## 4. 项目约定：CLAUDE.md / AGENTS.md 的加载策略

Hermes 的优先级（先匹配先生效）：

| 优先级 | 文件 | 搜索范围 |
| --- | --- | --- |
| 1 | `.hermes.md` / `HERMES.md` | CWD 向上至 git 根 |
| 2 | `AGENTS.md` | 仅 CWD |
| 3 | `CLAUDE.md` | 仅 CWD |
| 4 | `.cursorrules` | 仅 CWD |

原则是「**只加载一种**」：多份约定同时进上下文会互相矛盾，模型会随机挑一条遵守。

写项目约定时，三条经验：

1. **写可执行的命令**（`测试用 pnpm test:unit`），不要写「注意测试」；
2. **写负向边界**（「不要改 `migrations/`」），但记住它只是提示，不是闸门；
3. **控制长度**：注入前截断（本仓库 4000 字符，Hermes 约 20k 字符）。

## 5. 压缩：有损折叠的三段保留

触发条件：`used_tokens / max_context_tokens >= compact_at`（默认 0.6）。

保留三段，折叠中间：

```
[system] [第一条 user 任务] [折叠摘要] [最近 keep_tail 条]
```

为什么保留第一条 user：它定义了「什么算完成」，丢了它，模型会忘记验收标准。

折叠摘要至少包含：折叠了多少条、期间用过哪些工具、最后一条模型说明、以及一句
「需要细节请重新读取，不要编造」。

**关键细节：配对清洗。** 压缩边界如果切在 assistant(tool_calls) 之后、
tool 结果之前，就会留下「没有调用方的 tool 消息」，真实 provider 会直接 400。

```python
while out and out[0].get("role") == "tool":
    out.pop(0)
```

实验 `lab03` 用 `assert_pairing()` 验证了这一点——这是那种「本地脚本模型不报错、
上线才炸」的典型坑。

## 6. 工具输出截断：两层防护

| 层 | 位置 | 作用 |
| --- | --- | --- |
| 工具内 | `fetch` 只取 8000 字符 | 防止单次抓取炸上下文 |
| 循环里 | `max_tool_chars=4000` | 兜底所有工具 |

截断保留头尾（文档 02 有解释）。被截断的部分要写清「已截断 N 字符」，
否则模型会以为拿到了全部内容。

## 7. 记忆写什么、不写什么

记忆不是日志，是**可复用的判断依据**。写：

- 用户偏好（语言、框架、代码风格、禁忌）；
- 项目事实（部署方式、测试命令、目录约定）；
- 踩过的坑（「这台机器上 `make` 需要先 `source env.sh`」）。

不写：

- 一次性的任务进度（用会话历史或 todo 工具）；
- 可以从代码直接读到的信息（重复且会过期）；
- 大段文档（用路径引用，让 Agent 需要时去读）。

上限保护（本仓库 `max_chars=8000`）：
超限时**拒绝写入**并提示先 consolidate，而不是静默截断——静默截断会丢历史。

## 8. 多代理时代的上下文分工

| 上下文类型 | 放在哪 | 为什么 |
| --- | --- | --- |
| 全局身份与人格 | `SOUL.md` | 每个会话都需要，且稳定 |
| 长期事实 | `MEMORY.md` | 跨会话复用 |
| 用户偏好 | `USER.md` | 跨项目复用 |
| 项目约定 | `AGENTS.md` | 跟仓库走，可 review |
| 程序性知识 | `SKILL.md` | 按需加载，可版本化 |
| 一次性探索 | 子代理上下文 | 用完即弃，不污染主线 |

最后一行是子代理存在的核心理由：**把会弄脏主上下文的工作隔离出去**
（见文档 06）。

## 9. 自测

1. 为什么把 `datetime.now()` 写进系统提示会让成本翻倍？还有哪些写法等价于它？
2. 冻结快照的产品语义是什么？如果产品要求「说完立刻记住」，你会怎么改？
3. 压缩时为什么必须保留第一条 user 消息？丢了会怎样？
4. 压缩边界为什么会产生「孤儿 tool 消息」？不处理会发生什么？
5. 记忆文件该写「项目用什么测试命令」还是「今天改了三个文件」？为什么？
6. `AGENTS.md` 和 `CLAUDE.md` 同时存在时，为什么只加载一种更好？
