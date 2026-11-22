# 03 · 权限与沙箱：闸门必须在代码里

> 对应站点：[安全边界与权限](https://agent.jeffjade.com/claude-code/security-permissions/)、
> [沙箱隔离机制](https://agent.jeffjade.com/claude-code/sandboxing/)、
> [工具审批（Hermes）](https://agent.jeffjade.com/hermes-agent/tools-system/)。
> 代码：[`mini_harness/policy.py`](../mini_harness/policy.py)、实验 [`lab02`](../labs/lab02_permissions.py)。

## 1. 三层闸门

```
第一层：模式 + 规则（应用层）     —— 这一次调用允不允许？
第二层：沙箱（系统层）            —— 就算允许，能碰到什么？
第三层：人工审批（流程层）        —— 现在这一刻，人点不点头？
```

三层是**叠加**关系，不是替代关系：

- 只有规则 → 模型可以用 `bash` 绕过路径限制（`cat /etc/passwd`）；
- 只有沙箱 → 沙箱内的破坏性操作（`rm -rf .`）照样执行；
- 只有审批 → 审批疲劳，人会无脑点「同意」。

## 2. 模式：决定「没写规则时」的默认行为

| 模式 | 行为 | 适用 |
| --- | --- | --- |
| `default` | 只读放行，写/执行逐次问 | 日常开发 |
| `acceptEdits` | 工作区内编辑自动接受 | 高频迭代、信任度高 |
| `plan` | 只读探索，写工具从 schema 里移除 | 大改前的规划 |
| `dontAsk` | 未预授权的一律拒绝，不弹窗 | CI、无人值守 |
| `bypassPermissions` | 跳过确认（沙箱与黑名单仍生效） | 一次性容器 |

两个容易误解的点：

1. **`acceptEdits` 不等于无限制**，它只自动接受「工作区内的编辑」，`bash` 仍要问；
2. **`bypassPermissions` 不是关掉一切**，灾难性命令黑名单和沙箱仍在（见下）。

## 3. 规则语法：`Tool(specifier)`

```jsonc
{
  "permissions": {
    "allow": ["Bash(npm run test *)", "Bash(git status *)", "Read"],
    "deny":  ["Bash(git push *)", "Read(./.env)"],
    "ask":   ["Bash(curl *)"]
  }
}
```

**求值顺序：deny → ask → allow，先匹配先生效。** 这条顺序保证了「deny 永远赢」——
组织可以把 `Bash(rm *)` 钉死在 deny，个人无法用 allow 绕过。

matcher 的三种语义（本仓库 `_match_spec`）：

| 工具类型 | spec 语义 | 例子 |
| --- | --- | --- |
| `bash` 类 | 命令 glob；复合命令按 `&&`/`;`/`|` 拆段逐段匹配 | `git status *`、`npm run test *` |
| 文件类 | 归一化相对路径 glob | `./.env`、`**/*.pem` |
| 其他 | 参数 JSON 的 glob | `delegate({"goal":"*测试*"})`（示意） |

复合命令必须拆段匹配：`git status && git push` 只匹配 `git status *` 是不够的，
必须让 `git push` 那一段也过一遍 deny。

## 4. 沙箱：规则之外的第二道墙

规则是「按名字匹配」，沙箱是「按能力限制」：

```python
def resolve_in_workspace(ctx, raw):
    resolved = (ctx.workspace / raw).resolve()
    if resolved != base and not resolved.is_relative_to(base):
        raise PermissionError(f"路径越界：{raw!r} 不在工作区 {base} 内（沙箱拒绝）")
```

要点：

- 用 `resolve()` 之后再判断（否则 `../`、符号链接、`~` 都能绕过）；
- 越界判断放在**策略层**（提前拒绝，不产生副作用）和**工具层**（防御性兜底）两处；
- `bash` 无法做路径级沙箱——这是为什么需要 OS 级隔离（容器、`chroot`、Windows Job Object）。

站点 [沙箱隔离机制](https://agent.jeffjade.com/claude-code/sandboxing/) 讲的正是后者：
路径限制 + 网络限制 + 进程隔离，由操作系统执行，模型改不了。

**结论：只靠 `resolve_in_workspace` 挡住 `cat /etc/shadow` 是不可能的，那要靠 OS 沙箱。**

## 5. 灾难性命令黑名单：bypass 也不放过

```python
DANGEROUS_PATTERNS = (
    (r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)*\s*/", "递归删除根目录"),
    (r"\bmkfs(\.\w+)?\b", "格式化文件系统"),
    (r"\bdd\s+.*of=/dev/", "直接写块设备"),
    (r"\bformat\s+[a-zA-Z]:", "格式化磁盘"),
    (r"\bshutdown\b|\breboot\b", "关机或重启"),
    (r":\(\)\s*\{\s*:\|:&\s*\};:", "fork 炸弹"),
)
```

这是「绝对不允许」清单，先于一切规则和模式判定。它的存在说明一件事：
**任何权限系统都需要一个不可配置的底线**，否则「一键放行」的诱惑迟早会突破它。

黑名单不能替代沙箱（正则永远有绕过），它的价值是挡住「手滑 + 模型幻觉」这种最常见的事故。

## 6. Hooks：组织级策略的插入点

Hook 与规则的分工：

- 规则回答「允不允许」；
- Hook 回答「执行前后还要做什么」，可以审计、脱敏、二次拦截。

求值顺序：`Hook → 规则 → 沙箱` 中 Hook 最靠前，因此组织策略不会被个人 allow 绕过。

```python
@hooks.on_before
def secret_guard(call, ctx):
    if call.name in {"write_file", "edit_file"}:
        if any(m in blob for m in ("sk-", "AKIA", "-----BEGIN PRIVATE KEY-----")):
            return "Hook 拦截：内容疑似包含密钥，请改用环境变量"
```

典型用途：格式化（写后跑 prettier）、审计（记录所有 `bash`）、合规（禁止写 `.env`）。

## 7. 审批：如何不让人崩溃

审批疲劳是权限系统最大的敌人。三个实践：

1. **按前缀授权高频命令**：`Bash(npm run test *)` 一次授权，永久少弹窗；
2. **让拒绝可解释**：拒绝原因要能指引下一步（「路径越界，请在工作区内操作」）；
3. **CI 用 `dontAsk`**：无人值守时宁可失败，也不要「等待审批」挂死流水线。

本仓库的 `_resolve_ask` 在「无审批人」时默认拒绝（fail closed），这是安全默认值：

```python
if self.config.auto_deny_unapproved or self.approver is None:
    return Decision("deny", "需要人工确认但当前无审批人")
```

## 8. 子代理的权限继承

子代理不是「另一个用户」，它继承父会话的模式与审批人，**不能自行提权**：

- 子代理工具组收窄（本仓库只给 `core`）；
- 深度上限 1，防止代理自我复制；
- 派生动作本身不弹窗（真正的风险在子代理内部的调用）。

站点里的对应经验：后台子代理不会再弹权限窗，只能用会话里已授予的权限，
未批准的调用会被自动拒绝。

## 9. 一张决策表

| 操作 | 建议模式 | 建议规则 |
| --- | --- | --- |
| 读代码、grep | `default` | `Read` 全放行 |
| 跑测试 | `default` + allow | `Bash(pytest *)`、`Bash(npm test *)` |
| 改源码 | `acceptEdits` | 工作区外写操作 deny |
| 改生产配置 | `default` + 人工 | 关键路径 deny + 双人审批 |
| 数据迁移脚本 | `plan` 先看，再 `default` 逐条批 | `Bash(*)` 也不要开 |
| CI 自动修复 | `dontAsk` | 只 allow 白名单命令 |

## 10. 自测

1. 为什么规则求值顺序是 deny → ask → allow，而不是反过来？
2. 只用路径检查（`resolve_in_workspace`）能挡住 `cat /etc/passwd` 吗？为什么？
3. `acceptEdits` 模式下，哪些操作仍然会弹窗？
4. 黑名单正则为什么不能替代沙箱？它的真实价值是什么？
5. 你的 CI Agent 应该用哪个模式？如果某条命令必须允许，怎么写规则最安全？
