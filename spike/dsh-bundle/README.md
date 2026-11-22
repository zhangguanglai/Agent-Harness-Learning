# spike/dsh-bundle — S0-2：banking-a 领域 bundle

把 [domain/banking_a](../../domain/banking_a/README.md) 的三个 MCP 工具接进 DSH，
并定义两档合规权限预设。对应 [docs/16 §1](../../docs/16-implementation-plan.md) 的 S0-2。

## 内容

| 文件 | 作用 |
| --- | --- |
| `package.json` | bundle 清单：`dsh.bundle.patch` + 展示元数据导出 |
| `cordis.patch.yml` | 插入 MCP 行 + 覆盖 `permission` 行（两个预设） |
| `locale/{en,zh}.json` | Plugin Manager 里的标题/描述 |
| `docs/DETAIL.md` | 详情页：能力、边界、限制 |
| `icon.svg` | 原创图标（盾牌 + 条款 + 通过标记） |
| `validate.py` | 三层校验：清单 / MCP 工具面 / DSH 组合 |

## patch 做了什么

```yaml
- insert:
    - id: mcp-banking-a
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: banking-a
        transport: stdio
        command: python
        args: ['-m', 'domain.banking_a.mcp_server']
        cwd: !!js process.env.BANKING_A_HOME || process.cwd()
        failOnStartupError: true

- id: permission
  config:
    presets:
      compliance-review: { sandbox: workspace-write, approval: ask }
      compliance-batch:  { sandbox: workspace-write, approval: never }
    defaultPreset: compliance-review
```

两个要点：

1. **整行替换**：patch 覆盖 `permission` 行时是替换整个 `config`，所以预设表要写全；
2. **cwd 必须可解析**：MCP server 用 `python -m domain.banking_a.mcp_server`，
   需要 `BANKING_A_HOME` 指向仓库根（或把包装进环境）。

## 校验

```bash
python spike/dsh-bundle/validate.py
```

三层：

| 层 | 检查 | 需要什么 |
| --- | --- | --- |
| 静态 | manifest / locale / 引用文件 / patch 关键字 | 无 |
| 协议 | `initialize` + `tools/list`，三个工具齐全 | Python |
| 组合 | 隔离 `DSH_HOME` 里跑 `dsh --dump-config`，确认行真的合进 profile | DSH 运行环境 |

最近一次结果：三层全部通过（组合层输出含 `mcp-banking-a`、`serverName: banking-a`、
`compliance-review`、`defaultPreset`）。

## 安装到 profile

> 以下动作会修改 DSH profile，属破坏性操作；确认后再执行。

```bash
# 方式一：Plugin Manager（推荐，会写 profile 并选择 bundle）
#   在 DSH 里：Plugins → Add plugin → 选择本目录
# 方式二：CLI
dsh plugin --profile <name> add <本目录绝对路径>
```

安装后验证：

```bash
dsh --profile <name> --dump-config | grep -E "mcp-banking-a|compliance-review"
```

启动新会话后，模型工具列表应出现 `mcp__banking-a__kb.search` 等三个工具；
Permissions 选择器应出现「Compliance review / Compliance batch pre-review」两档。

**回滚**：Plugin Manager 里移除该 bundle，或在 profile 里删掉对应行；
不涉及数据迁移。

## 已知限制

- MCP server 依赖 `python` 在 PATH 且能 import `domain.banking_a`（用 `BANKING_A_HOME` 指路）；
- `failOnStartupError: true` 意味着 MCP 起不来会拒绝激活，这是刻意的（宁可不启动，也不要静默少工具）；
- 预设里的 `compliance-batch`（approval: never）只用于离线批量预审，**不得用于生产申报**；
- 合成数据与阈值仍需替换与校准（见 domain README）。
