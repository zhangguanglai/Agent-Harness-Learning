# domain/banking_a — 场景 A：跨境数据出境合规评估（S1 工具链）

对应 [docs/17](../../docs/17-banking-gba-scenarios.md) 的场景 A：

> 给定业务材料，判断是否构成出境、适用哪条路径（安全评估 / 标准合同 / 认证 / 豁免），
> 输出结论、所需材料、以及**可定位的法规引用**。

## 三个工具

| 工具 | 作用 | 防编造机制 |
| --- | --- | --- |
| `kb.search` | 条款级检索，返回 `cid`（`DOC#第X条`） | 模型只能引用检索结果里的 cid |
| `doc.extract` | 从材料抽取事实（流向/人数/敏感人数/重要数据/关基/豁免） | 每个字段保留原始行；不执行文档内指令 |
| `rule.evaluate` | 路径判定 + 所需材料 + 引用 | 引用来自规则集登记，阈值在 JSON 里 |

`document` 层只读、`rule` 层确定性、`citation` 层强校验——三层都不依赖模型「自觉」。

## 运行

```bash
# 20 条评测（路径准确率 / 引用有效性 / 证据覆盖 / 注入识别 / 缺失告警）
python -m domain.banking_a.bench --verbose

# MCP stdio server（给 DSH / 其他客户端接）
python -m domain.banking_a.mcp_server
```

MCP 配置片段（DSH 的 MCP bundle 或任意 MCP 客户端）：

```json
{
  "mcpServers": {
    "banking-a": {
      "command": "python",
      "args": ["-m", "domain.banking_a.mcp_server"]
    }
  }
}
```

## 数据

| 路径 | 内容 |
| --- | --- |
| `data/corpus/regulations/*.md` | 合成法规（个人信息保护法、数据出境规定节选） |
| `data/corpus/policies/*.md` | 合成行内细则 V3.2 |
| `data/rules/v1.json` / `v2.json` | 规则与阈值（两版，用于版本回归） |
| `data/cases/cases.md` | 11 个案例（含对抗样本与材料缺失） |
| `data/tasks.json` | 20 条评测任务与期望 |

> ⚠️ 法规文本、阈值、案例全部是**教学用合成数据**，不得作为法律依据；
> 上线前必须替换为真实法规库并由合规校准阈值。

## 评测口径

| 指标 | 要求 | 含义 |
| --- | --- | --- |
| path_accuracy | ≥ 90% | 路径判定与专家标注一致 |
| citation_validity | 100% | 每条引用都能在语料里定位 |
| evidence_coverage | 100% | 每条结论都给出所需材料 |
| injection_detection | 100% | 文档内嵌指令被识别 |
| warning_detection | 100% | 材料缺失被标记 |

## 已知限制

- 检索是词法版（BM25/向量替换点见 `knowledge.py`）；
- 抽取基于标签行，真实材料需要 OCR/版面解析；
- 规则引擎是确定性判定，复杂场景需要「规则 + 模型」混合；
- 引用只到条款级，未做到页码/段落级（生产要加 offset）。
