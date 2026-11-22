# 02 · 工具系统：把意图变成受控的副作用

> 对应站点：[工具系统（Hermes）](https://agent.jeffjade.com/hermes-agent/tools-system/)、
> [Skills/Plugins 之外的工具约定](https://agent.jeffjade.com/claude-code/plugins/)、
> [架构拆解 · tools/registry.py](https://agent.jeffjade.com/hermes-agent/architecture-deep-dive/)。
> 代码：[`mini_harness/tools.py`](../mini_harness/tools.py)。

## 1. 工具是 Harness 的「API 契约」

一个工具由四样东西定义，缺一不可：

```python
Tool(
    name="read_file",
    description="读取工作区内一个文本文件……只读；读过的文件才允许 edit_file。",
    parameters={...},        # JSON Schema，决定模型怎么填参
    handler=fn,              # (args, ctx) -> str，真正的执行
    read_only=True,          # 决定能否并行、能否在 plan 模式使用
    group="core",            # toolset 分组，决定在哪些入口暴露
)
```

只有这四样都明确，模型才可能「稳定地用对」。实践中最常见的失败不是 handler 写错，
而是 **description 写成了产品文案**：

- 差：「读取文件」——模型不知道路径相对谁、能不能读工作区外、大文件会怎样；
- 好：「读取工作区内一个文本文件，可选行号范围。只读；读过的文件才允许 edit_file。
  路径相对工作区，越界会被沙箱拒绝。」

工具的 description 是**给模型看的 API 文档**，应该和给同事看的 SDK 文档一样精确。

## 2. 自注册：为什么不要中央 import 清单

Hermes 的做法：每个 `tools/*.py` 在 import 时调用 `registry.register(...)`，
新增工具 = 新增一个文件，不需要改中央列表。

```python
# tools.py（节选）
def build_core_registry() -> ToolRegistry:
    reg = ToolRegistry()

    @reg.tool(name="list_dir", ...)
    def list_dir(args, ctx): ...

    @reg.tool(name="read_file", ...)
    def read_file(args, ctx): ...
    return reg
```

自注册的收益是**扩展成本恒定为 1 个文件**；代价是**加载顺序变得有意义**：

- 工具必须在 discovery 链上被 import，否则「注册了但不存在」；
- 插件加载失败要能降级，不能把整个 registry 带崩。

站点 [架构拆解](https://agent.jeffjade.com/hermes-agent/architecture-deep-dive/) 里
「改了 `tools/foo.py` 却未生效」这个故障，根因就是前者。

## 3. 错误必须变成对话，不能变成异常

这是 Harness 与普通后端最大的工程差异。

后端思维：handler 抛异常 → 500 → 调用方重试。
Harness 思维：handler 抛异常 → **变成一条 tool 消息** → 模型读到错误 → 换策略。

```python
# ToolRegistry.call
try:
    output = tool.handler(call.arguments, ctx)
    ok, error = True, ""
except Exception as exc:
    output, ok, error = "", False, f"{type(exc).__name__}: {exc}"
```

为什么必须这样：

1. 模型是唯一能「理解这个错误并换路」的角色；
2. 异常穿透循环会让整条任务链丢失已完成的进度；
3. 错误文本本身是最有效的纠错提示（比如 `old_string 未匹配` 会促使模型重新读文件）。

**反例**：`subprocess.run(..., check=True)` 直接抛 `CalledProcessError`。
正确做法是拿 `returncode` 和 `stderr` 组一条结果，并告诉模型「非零退出，修复后重试」。

## 4. 输出裁剪：头尾保留

工具返回 20 万字符的日志会瞬间吃光上下文。裁剪策略不是简单截断：

```python
def truncate_middle(text, limit):
    head = keep * 3 // 5
    tail = keep - head
    return text[:head] + f"…（已截断 {n} 字符）…" + text[-tail:]
```

因为模型的判断依赖**开头**（结论、摘要）和**结尾**（报错、堆栈最后一行）。
只留开头会把最关键的错误信息丢掉。

裁剪发生在两层：

- 工具内（`fetch` 只取前 8000 字符）；
- 循环里（`AgentConfig.max_tool_chars`，默认 4000，实验 `lab03` 验证）。

## 5. toolset：对模型暴露「当前有用的工具」

工具越多，schema 越占 token，选错工具的概率越高。Hermes 用 `toolsets.py` 分组，
按平台/场景只暴露一组：

```
minimal = {read_file, list_dir, run_shell}
web     = minimal + {web_fetch}
full    = 全部
```

本仓库用 `Tool.group` + `AgentConfig.tool_groups` 实现同一件事：

```python
tool_groups=("core", "shell", "memory", "skills", "delegate")
```

注意一个反直觉的点：**工具面切换会破坏 prompt 前缀缓存**。所以成熟实现让工具集变更
默认「下一个会话生效」，而不是中途热插拔（见文档 04 的 prompt 稳定性）。

## 6. 五个起步工具就够验证闭环

Hermes 的「从零实现」章节给的最小集合，值得直接抄：

| 工具 | 为什么必须有 |
| --- | --- |
| `read_file` / `list_dir` | 没有观察就没有事实，Agent 会开始编 |
| `write_file` | 交付物 |
| `edit_file` | 精确改动，比整文件重写安全 |
| `bash` | 验证（测试、构建、git） |
| `fetch` | 外部信息 |

本仓库额外加了 `memory` / `skill_view` / `skill_manage` / `delegate`，属于「让 Agent
变聪明」的那一类，不是闭环必需。

## 7. `edit_file` 的前置约束：先读再改

Claude Code 的 Edit 有一条硬规则：文件必须被读过，且磁盘内容未变，`old_string`
必须唯一。这条规则应该下沉到工具层，而不是写在提示词里：

```python
if rel not in ctx.read_paths:
    raise PermissionError(f"拒绝盲改：本会话尚未读取 {rel}。请先 read_file 再 edit_file")
count = text.count(old)
if count > 1 and not replace_all:
    raise ValueError(f"old_string 匹配到 {count} 处，请加长上下文或设置 replace_all=true")
```

三个收益：

1. 少一类「模型凭记忆改错文件」的事故；
2. 错误信息直接告诉模型下一步做什么（重新读）；
3. 让 diff 可审查——精确替换比整文件覆盖更容易 review。

## 8. 工具设计检查清单

写完一个工具，逐条过：

- [ ] name 是动词开头、snake_case，且与权限规则里写的一致；
- [ ] description 说清：做什么、边界在哪、失败会怎样、是否只读；
- [ ] parameters 每个字段有类型和说明，`required` 只放真正必需的；
- [ ] 所有路径都经过 `resolve_in_workspace`，不允许裸 `open(path)`；
- [ ] 异常会被 `ToolRegistry.call` 捕获成 tool 消息；
- [ ] 输出有上限，超长时头尾保留；
- [ ] 毒副作用类工具（写、执行、网络）`read_only=False`；
- [ ] 有一个最小可跑的单元实验（本仓库 `labs/` 的风格）。

## 9. 自测

1. 为什么工具 handler 抛异常不应该让循环崩掉？举一个「错误进对话后模型自我修复」的例子。
2. 工具 description 写「读取文件」会带来哪三类误用？
3. `read_only` 标记同时影响哪三件事？（并行、plan 模式、默认审批）
4. 输出截断为什么保留尾部而不是只留开头？
5. 如果让你给这个 Harness 加「数据库查询」工具，权限规则该怎么写才不至于天天弹窗？
