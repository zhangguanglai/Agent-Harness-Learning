# Agent Harness 学习工程

一套**可运行**的 Agent Harness 学习材料：以 [智能体漫游](https://agent.jeffjade.com/)
（Claude Code 45 篇 + Hermes Agent 17 篇）为参照，把散落在两套产品里的机制，
重组为「九个部件」的架构视角，并用一个**零依赖的 Python 极简实现**逐层验证。

- 📚 **读**：`docs/` 10 篇分层专题 + `docs/appendix/` 18 册章节精读笔记（约 60 章）
- 🛠 **跑**：`mini_harness/` 循环 / 工具 / 权限 / 上下文 / 记忆 / 技能 / 子代理 / 轨迹
- ✅ **验**：`labs/` 7 个实验 + 一键验收，**不联网、不需要 API Key**

```
环境：Python 3.11+（仅标准库，无需 pip install）
```

## 快速开始

```bash
# 1. 看一次完整的多轮工具循环（离线，4 轮）
python -m mini_harness demo

# 2. 一键验收全部机制
python labs/verify.py
```

`demo` 会：列目录 → 读 README → 写 NOTES.md → 收尾，并把完整轨迹落到
`.mini-demo/workspace/.mini/traces/*.jsonl`。

接入真实模型（任何 OpenAI 兼容服务）：

```bash
export OPENAI_API_KEY=sk-...
python -m mini_harness run "给 README 加一节快速开始" \
  --provider openai --model deepseek-chat --base-url https://api.deepseek.com/v1
```

## 目录

```
.
├── README.md                    # 本文件：路线、映射、取舍
├── AGENTS.md                    # 项目约定（会被注入 Agent 的 system 提示，用于演示）
├── mini_harness/                # 极简 Harness 实现（~1600 行，纯标准库）
│   ├── loop.py                  #   代理循环、停止条件、提示组装、子代理
│   ├── tools.py                 #   工具注册表 + 6 个内置工具 + 沙箱路径解析
│   ├── policy.py                #   模式 / 规则 / 沙箱 / 灾难命令黑名单
│   ├── context.py               #   预算、截断、压缩、配对清洗
│   ├── memory.py                #   SOUL/MEMORY/USER + 冻结快照
│   ├── skills.py                #   L0 索引 / L1 全文 / 自沉淀
│   ├── subagent.py              #   delegate 工具
│   ├── hooks.py                 #   执行前后钩子（密钥拦截等）
│   ├── trace.py                 #   JSONL 轨迹 + token 账本
│   ├── llm.py                   #   脚本模型 / 演示模型 / OpenAI 兼容客户端
│   └── cli.py                   #   demo / run / repl
├── labs/                        # 7 个实验 + verify.py
├── docs/                        # 10 篇分层专题
│   └── appendix/                # 18 册章节精读（对应站点 60+ 章）
└── skills/                      # 两个示例 Skill（渐进披露的素材）
```

## 读什么：十一个分层文档

| 文档 | 主题 | 一句话 |
| --- | --- | --- |
| [00](docs/00-harness-architecture.md) | 全景 | Harness 的九个部件、三条设计主线 |
| [01](docs/01-agent-loop.md) | 代理循环 | 八行循环 + 五种停止出口 + Plan Mode |
| [02](docs/02-tools-protocol.md) | 工具系统 | schema 是契约，错误要进对话 |
| [03](docs/03-permissions-sandbox.md) | 权限与沙箱 | 闸门在代码里，不在提示词里 |
| [04](docs/04-context-memory.md) | 上下文与记忆 | 前缀稳定、冻结快照、有损折叠 |
| [05](docs/05-skills.md) | Skill | 渐进披露，按需加载程序性记忆 |
| [06](docs/06-subagents-multiagent.md) | 子代理 | 隔离比聪明更重要 |
| [07](docs/07-mcp-plugins-gateway.md) | 扩展与多入口 | 一个内核，多种入口 |
| [08](docs/08-observability-cost.md) | 可观测与成本 | 先看见，再优化 |
| [09](docs/09-security-and-org.md) | 安全与组织 | 威胁模型、落地四阶段、心智迁移 |
| [10](docs/10-learning-path.md) | 学习路径 | 四周路线 + 40 题自测 + 总映射 |
| [11](docs/11-production-harness.md) | 生产化 | 从教学实现到生产 Harness 的规划、架构与必做扩展 |

## 章节 → 笔记 映射

站点两个专题共 62 篇，按「部分」提炼为 18 册笔记：

| 专题 | 部分 | 分册 |
| --- | --- | --- |
| Claude Code | 一 基础认知 | [cc-p1](docs/appendix/cc-p1.md) |
| | 二 快速上手 | [cc-p2a](docs/appendix/cc-p2a.md) · [cc-p2b](docs/appendix/cc-p2b.md) |
| | 三 工作原理 | [cc-p3](docs/appendix/cc-p3.md) |
| | 四 项目记忆 | [cc-p4](docs/appendix/cc-p4.md) |
| | 五 高级扩展 | [cc-p5](docs/appendix/cc-p5.md) |
| | 六 Skill 体系 | [cc-p6](docs/appendix/cc-p6.md) |
| | 七 实战 | [cc-p7a](docs/appendix/cc-p7a.md) · [cc-p7b](docs/appendix/cc-p7b.md) |
| | 八 反思与进阶 | [cc-p8](docs/appendix/cc-p8.md) |
| | 九 进阶实践 | [cc-p9a](docs/appendix/cc-p9a.md) · [cc-p9b](docs/appendix/cc-p9b.md) |
| | 十 排障速查 | [cc-p10](docs/appendix/cc-p10.md) |
| Hermes | 一 入门 | [hermes-p1](docs/appendix/hermes-p1.md) |
| | 二 核心机制 | [hermes-p2](docs/appendix/hermes-p2.md) |
| | 三 实战 | [hermes-p3](docs/appendix/hermes-p3.md) |
| | 四 进阶 | [hermes-p4a](docs/appendix/hermes-p4a.md) · [hermes-p4b](docs/appendix/hermes-p4b.md) |

## 实验清单

| 实验 | 验证的机制 | 关键断言 |
| --- | --- | --- |
| [lab01](labs/lab01_loop.py) | 循环与停止条件 | 自然/轮次/预算/轨迹 |
| [lab02](labs/lab02_permissions.py) | 权限三层 | 越界不落地、bypass 仍拦黑名单、连续拒绝即停 |
| [lab03](labs/lab03_context.py) | 上下文压缩 | token 下降、任务保留、工具配对 |
| [lab04](labs/lab04_memory.py) | 记忆冻结 | 同会话 system 不含新记忆 |
| [lab05](labs/lab05_skills.py) | Skill 渐进披露 | 索引无正文、自沉淀生效 |
| [lab06](labs/lab06_subagent.py) | 子代理隔离 | 父上下文无子步骤 |
| [lab07](labs/lab07_trace_cost.py) | 轨迹与账本 | seq 连续、账本一致、可归因 |

单独运行：`python labs/lab03_context.py`；全部：`python labs/verify.py`。

## 设计取舍（为什么代码长这样）

| 取舍 | 选择 | 理由 |
| --- | --- | --- |
| 依赖 | 只用标准库 | 零安装才能「读完就能跑」 |
| 模型 | 可替换协议 + 脚本模型 | 离线、确定性，实验可断言 |
| 消息格式 | OpenAI 风格 dict | 跨家事实标准，边界转换最省事 |
| 存储 | JSONL 轨迹 / Markdown 记忆 | 人类可读、可 diff、可 git |
| 记忆 | 冻结快照 | 缓存友好、语义清晰（照抄 Hermes） |
| 权限 | 规则 + 模式 + 沙箱三层 | 单层都能被绕，叠加才有效 |
| 子代理 | 一个工具 + 新上下文 | 多代理不需要新协议 |
| 压缩 | 有损折叠 + 配对清洗 | 简单、可预测、不依赖第二次模型调用 |

**没有实现**（刻意留白，避免教程变成半成品框架）：向量检索、真实 MCP 客户端、
多平台 Gateway、图形界面、分布式执行。这些在 `docs/appendix/` 里有机制说明，
需要时按 [10 学习路径](docs/10-learning-path.md) 的练习自行扩展。

## 常见问题

**Q：为什么不直接用 Claude Code / Hermes？**
A：它们是产品，学的是「怎么用」；这里是「怎么造」。造一遍再回去用，才知道哪些行为是
设计取舍、哪些是 bug。

**Q：脚本模型（`DemoLLM` / `ScriptedLLM`）算不算作弊？**
A：它们是**测试替身**。Harness 的正确性（停止条件、权限、压缩、隔离）与模型智能无关，
用确定性模型才能写出可断言的实验。真实模型接入只需换 `--provider`。

**Q：`mini_harness` 能用于生产吗？**
A：不能。它是教学实现：没有 OS 级沙箱、没有并发会话管理、没有重试与限流、
没有多平台适配。生产请用成熟 Harness，或至少补上 `docs/09` 清单里的项。

**Q：Windows 下中文/emoji 报编码错？**
A：CLI 与 labs 已把输出切到 UTF-8。若仍异常，先执行 `chcp 65001`。

**Q：在受限沙箱（DSH / Codex 等）里 `git push` 报 `sh.exe: couldn't create signal pipe, Win32 error 5`？**
A：Git for Windows 自带的 `ssh.exe` 与 `sh.exe` 是 cygwin 程序，沙箱不允许它们创建命名管道；
而 `core.sshCommand` / `GIT_SSH_COMMAND` 又会被 `sh -c` 包一层，同样走不通。改用 Windows 自带的原生 OpenSSH：

```powershell
$env:GIT_SSH='C:\Windows\System32\OpenSSH\ssh.exe'
git push
```

并在 `~/.ssh/config` 里为 GitHub 指定密钥（本仓库作者本机的配置）：

```
Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/id_ed25519_agent_harness
  IdentitiesOnly yes
  UpdateHostKeys no   # 沙箱里无法改写 known_hosts，关掉轮换以免告警
```

`setx GIT_SSH "C:\Windows\System32\OpenSSH\ssh.exe"` 可让新开的终端永久生效。
另外两条路：改用 HTTPS + PAT（完全不碰 ssh），或干脆在沙箱外的普通终端推送。

## 来源与声明

- 笔记与对照基于 [智能体漫游](https://agent.jeffjade.com/)（Claude Code / Hermes Agent 专题），
  是**学习笔记**而非官方文档；版本、命令、价格等以官方为准。
- 代码为本仓库原创，MIT 许可。
- 站点版权归其作者所有；引用处均标注原文链接。
