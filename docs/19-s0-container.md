# 19 · S0-1：把 DSH 关进容器

> 目标：本机能跑的链路，在隔离环境里也能跑。
> 资产：[deploy/s0-container](../deploy/s0-container/README.md)、[Dockerfile](../deploy/s0-container/Dockerfile)、
> [entrypoint.sh](../deploy/s0-container/entrypoint.sh)、[verify.py](../deploy/s0-container/verify.py)。

## 1. 为什么不是「装个 Docker 就完了」

容器化在这里要解决三件事，而不是「打个镜像」：

| 问题 | 做法 |
| --- | --- |
| 运行时依赖多（Node 24 + dsh + python3 + bundle） | 镜像内固定版本，一次构建到处运行 |
| 执行面危险（bash、文件写、MCP 子进程） | 非 root、只读根、cap-drop、资源限额 |
| 模型需要出网，其余不该出网 | 默认拒绝 + egress 白名单（模型域名） |

## 2. 镜像分层

```
node:24-bookworm-slim
 ├─ apt: python3, ca-certificates          （MCP server 用）
 ├─ npm -g @deepseek-ai/dsh@0.2.2-rc.5    （固定版本）
 ├─ /opt/banking-a/bundle                  （cordis.patch.yml + locale + icon）
 ├─ /opt/banking-a/domain                  （banking_a 工具与语料）
 ├─ /opt/banking-a/worker                  （dsh-worker：headless/worker 两用）
 └─ user harness(10001), WORKDIR /workspace
```

`headless` 模式：`dsh headless --patch /opt/banking-a/bundle/cordis.patch.yml "$TASK"`；
`worker` 模式：`node /opt/banking-a/worker/src/server.js`（SDK 后端）。

## 3. 安全默认

| 措施 | 值 | 防什么 |
| --- | --- | --- |
| 非 root | uid 10001 | 逃逸后的权限 |
| 只读根 | `--read-only` + tmpfs `/tmp` | 篡改镜像内文件 |
| 去能力 | `--cap-drop ALL`、`no-new-privileges` | 提权 |
| 资源限额 | `--pids-limit 256`、`--memory 2g`、`--cpus 2` | fork 炸弹、资源争抢 |
| 工作区卷 | 唯一可写挂载 | 产物与镜像分离，便于审计 |
| 网络 | 默认拒绝 + 白名单 | 数据外流、SSRF |

> 「禁网」要精确表述：**工具执行默认无出网，仅放行模型 API**。完全禁网时模型不可用，
> 除非把模型网关以 sidecar/localhost 方式放进同一网络命名空间。

## 4. 验证矩阵

| 层 | 检查 | 本机结果 |
| --- | --- | --- |
| 静态 | Dockerfile 约束、entrypoint 关键命令 | ✅ PASS |
| 环境 | docker CLI / daemon | ⚠️ daemon 未运行 |
| 构建 | `docker build` | ⏸ 待有 Docker 的环境 |
| 运行 | 容器内完成「列目录 + 写 HELLO.md」 | ⏸ 同上 |

```bash
python deploy/s0-container/verify.py --static-only   # 0
python deploy/s0-container/verify.py                 # 2 = 跳过（无 daemon）
python deploy/s0-container/verify.py --build         # 有 Docker 时：0 / 1
```

`verify.py` 对「跳过」返回 2，而不是 0——避免把「没验证」当成「通过」。

## 5. 已知限制

| 限制 | 影响 | 下一步 |
| --- | --- | --- |
| 镜像未签名、无 SBOM | 供应链不可审计 | cosign + syft |
| 冷启动无预热 | 首轮时延高 | 预热池（K8s/KEDA） |
| 单容器单 run（worker 模式可多 run） | 隔离粒度不够 | 每 run 一容器（Phase 1） |
| 无 GPU/内网模型适配 | 只能用公网/网关 | 内网网关 + egress 白名单 |

## 6. 自测

1. 为什么 `--read-only` 必须配合 tmpfs，否则哪些常见操作会失败？
2. 为什么「完全禁网」和「跑真实模型」在 S0-1 里是矛盾的？正确表述是什么？
3. `verify.py` 为什么把「无 daemon」设为退出码 2 而不是 0？
4. 如果模型走行内网关，容器网络该怎么配？
5. 镜像里为什么要带 `domain/`，而不是运行时挂载？
