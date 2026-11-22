"""场景 A：跨境数据出境合规评估（教学用合成数据）。

模块：
- `knowledge`：条款级检索（带 citation id）
- `extract`：材料事实抽取（含注入检测）
- `rules`：规则引擎（路径判定 + 所需材料 + 引用）
- `mcp_server`：把三个工具暴露为 MCP stdio server
- `bench`：20 条任务的自动验收

所有法规文本与阈值均为合成示例，不得作为法律依据。
"""
