# AGENTS.md

> 这个文件会被 `mini_harness` 注入到 Agent 的系统提示里（见 `loop.Agent._project_context`）。
> 它同时是本仓库的真实约定 —— 用来演示「项目记忆」这一层。

## 项目是什么

一个用于学习 Agent Harness 的教学仓库：`mini_harness/` 是极简实现，`labs/` 是可验收实验，
`docs/` 是分层笔记。**代码第一目标是可读，第二目标才是功能。**

## 硬约束

- 只用 Python 标准库，不引入第三方依赖（包括 pytest、requests）。
- Python 3.11+；不使用需要 3.12+ 的语法。
- 所有面向用户的输出用简体中文；标识符用英文。
- 不得删除 `labs/` 下的任何断言来「让测试通过」。

## 代码风格

- 模块开头用一段 docstring 说明「这个文件在教什么」。
- 公开函数写类型标注；内部函数可以省略。
- 工具 handler 的签名固定为 `(args: dict, ctx: ToolContext) -> str`，异常交给注册表兜底。
- 新增机制时，同步更新：`docs/` 对应文档 + 一个 `labs/labXX_*.py` 断言。

## 验证

```bash
python labs/verify.py       # 必须全绿
python -m mini_harness demo # 必须出现「停止原因：final」
```

## 不要做的事

- 不要在系统提示里注入时间戳或随机 id（会破坏前缀缓存）。
- 不要让工具绕过 `resolve_in_workspace` 直接读写路径。
- 不要把权限判断写进工具 handler（应在 `policy.py`），工具层只做防御性兜底。
- 不要在 `lab` 里依赖网络或真实 API Key。
