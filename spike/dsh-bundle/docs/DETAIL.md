# 跨境数据出境评估（banking-a bundle）

## 它做什么

把 `domain/banking_a` 的三个工具通过 MCP 接进 DSH，并提供两档合规权限预设。

| 工具 | 名称（模型可见） | 作用 |
| --- | --- | --- |
| `kb.search` | `mcp__banking-a__kb.search` | 条款级检索，返回 `cid`（`DOC#第X条`） |
| `doc.extract` | `mcp__banking-a__doc.extract` | 从案例抽取事实并标记注入文本 |
| `rule.evaluate` | `mcp__banking-a__rule.evaluate` | 判定出境路径、所需材料与引用 |

| 权限预设 | sandbox | approval | 用途 |
| --- | --- | --- | --- |
| `compliance-review` | workspace-write | ask | 日常评估：写工作区逐次确认 |
| `compliance-batch` | workspace-write | never | 离线批量预审；**对外提交仍须人工** |

## 边界

- 工具只做「检索 / 抽取 / 判定」，**不写申报材料、不对外提交**；
- 结论必须带引用；引用不合法时工具返回 `isError`，不会静默放过；
- 合成法规与阈值仅用于演示，生产必须替换并校准；
- 批量预审预设降低交互，不降低责任：对外动作一律人工审批。
