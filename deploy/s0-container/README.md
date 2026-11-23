# S0-1：容器内运行 DSH（headless / worker）

把「本机能跑」变成「隔离环境能跑」。对应 [docs/16](../../docs/16-implementation-plan.md) 的 S0-1。

## 内容

| 文件 | 作用 |
| --- | --- |
| `Dockerfile` | `node:24-slim` + Python3 + `@deepseek-ai/dsh@0.2.2-rc.5` + bundle/domain/worker |
| `entrypoint.sh` | `headless` 一次性任务 / `worker` 常驻服务 / `shell` 排查 |
| `docker-compose.yml` | worker 服务（只读根、cap-drop、资源限制） |
| `verify.py` | 静态检查 + 构建 + 容器内跑任务并断言产物 |

## 构建与运行

```bash
# 构建（在仓库根目录执行）
docker build -f deploy/s0-container/Dockerfile -t banking-a/s0:local .

# headless：容器内完成「列目录 + 写 HELLO.md」
docker run --rm \
  --read-only --tmpfs /tmp:rw,size=64m \
  --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit 256 --memory 2g --cpus 2 \
  -e DEEPSEEK_API_KEY \
  -v "$PWD/workspace:/workspace" \
  banking-a/s0:local headless

cat workspace/HELLO.md
```

`headless` 默认任务可用 `-e TASK="..."` 覆盖。bundle patch 已内置
（`/opt/banking-a/bundle/cordis.patch.yml`），因此 `mcp__banking-a__*` 工具在容器里同样可见。

## worker 模式

```bash
export DEEPSEEK_API_KEY=sk-...
docker compose -f deploy/s0-container/docker-compose.yml up --build
curl -s localhost:8080/healthz
```

## 安全默认

| 措施 | 原因 |
| --- | --- |
| 非 root（uid 10001） | 减少逃逸后的权限 |
| `--read-only` + tmpfs `/tmp` | 根文件系统不可写，产物只落工作区卷 |
| `--cap-drop ALL` + `no-new-privileges` | 去掉提权面 |
| `--pids-limit` / `--memory` / `--cpus` | 限制 fork 炸弹与资源争抢 |
| 工作区独立卷 | 产物与镜像分离，便于审计与回收 |

## 网络策略（对「禁网」的修正）

DSH 跑真实模型时**必须**能访问模型 API。所以 S0-1 的验收不是「完全禁网」，
而是「默认无出网，仅放行模型域名」：

- 生产：egress 白名单（`api.deepseek.com` 或行内网关域名），其余拒绝；
- 本地：先用默认 bridge 验证功能，再套网络策略；
- 若模型走行内网关，把网关地址加入白名单即可，无需开放公网。

## 验证（已通过）

```
静态检查：PASS（Dockerfile / entrypoint）
Docker：可用 29.9.3
运行：PASS 容器内完成任务（7s），HELLO.md='ok'
```

```bash
python deploy/s0-container/verify.py --static-only   # 只跑静态检查
python deploy/s0-container/verify.py --build         # 构建 + 运行 + 断言 HELLO.md
```

| 退出码 | 含义 |
| --- | --- |
| 0 | 静态 + 构建 + 容器内任务全部通过（本机已通过） |
| 1 | 失败 |
| 2 | 跳过（无 Docker daemon 或缺 Key） |

> 2026-11-23 本机（Docker 29.9.3 / linux x86_64）已跑通：容器内 headless 用时 7s 写出 `HELLO.md=ok`。

## 已知限制

- 镜像内固定 DSH 版本（`0.2.2-rc.5`），升级需重建并重跑契约测试；
- MCP server 依赖容器内 `python3` 与 `/opt/banking-a/domain`（镜像已内置）；
- `headless` 每次冷启动（无预热池），时延高于常驻 worker；
- 镜像未做签名与 SBOM，生产需补供应链环节。

## 容器里的四个坑（实测）

1. **只读根 + 可写 DSH_HOME**：DSH 要创建 profile，必须把 `DSH_HOME` 指到 tmpfs（本镜像用 `/tmp/dsh-home`），否则 `mkdir /home/harness/.dsh/...` 报 ENOENT；
2. **tmpfs 必须带 `exec`**：默认 `size=` 挂载常带 `noexec`，原生模块 dlopen 会报
   `failed to map segment from shared object`；运行参数用 `--tmpfs /tmp:rw,exec,size=256m`；
3. **npm 全局安装的 native addon 解析**：`node-addon-require-builtin-*` 被提升到 `dsh/node_modules`，
   loader 却找主包内路径；镜像里补了平台包软链与 `build/napi/...` 本地构建别名；
4. **`python` 别名**：MCP server 命令是 `python -m domain...`，Debian 只有 `python3`；镜像加了 `/usr/local/bin/python` 软链。
