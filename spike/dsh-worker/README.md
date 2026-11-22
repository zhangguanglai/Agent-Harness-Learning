# spike/dsh-worker — S0：HTTP ↔ DSH SDK 适配骨架

把 DeepSeek Harness（DSH）的 **SDK stdio runtime** 包装成一个可被控制面调度的 HTTP worker：
接单 → 转发事件（JSON/SSE）→ 审批往返 → 取消。

> 这是 S0 验证件（见 [docs/16](../../docs/16-implementation-plan.md)），不是生产实现。
> 生产形态见 [docs/15](../../docs/15-option-c-technical-design.md)。

## 已验证的协议事实（DSH 0.2.2-rc.5）

来源：本机安装的 `@deepseek-ai/dsh-sdk-protocol` 与 `dsh-sdk-jsonrpc-server` 文档。

- 传输：**一行一帧的 JSON-RPC 2.0**，stdout 只允许协议帧，诊断走 stderr；
- 客户端方法：`initialize`、`session/query`、`session/prompt`、`session/resume`、
  `session/wait`、`session/working-directory/*`、`shutdown`；
- 服务端请求：`approval/request` → `{ outcome: "allowed-once" | "rejected" | "cancelled" }`；
- 服务端通知：`session.event`、`session.status`、`session.query.completed`、`subagent.*`；
- **没有取消方法**：官方说明「客户端通过关闭 runtime 进程放弃一轮」——
  所以本 worker 的 `POST /cancel` 实现为**杀进程组**，而不是发 RPC。

## 分层

```
src/server.js          HTTP 路由：/v1/runs、/events(SSE)、/cancel、/healthz
src/runner.js          RunManager：状态机、事件信封、审批往返、终态回收
src/protocol.js        事件信封 + DSH 通知归一化（上游变化只改这里）
src/backends/session.js  握手 + prompt + 审批应答（与传输解耦）
src/backends/sdk.js       spawn `dsh --profile sdk`，stdio JSON-RPC，杀进程组
src/backends/loopback.js  进程内 PassThrough（沙箱/CI 用）
src/testing/mock-runtime.js  协议替身（同时供 bin/mock-dsh.js 与 loopback 使用）
bin/mock-dsh.js       子进程形态的 mock DSH
probe.js              连真实 DSH，录制原始帧（S0-3 后续步骤）
```

## 快速开始（离线，不需要模型 Key）

```bash
cd spike/dsh-worker
node src/server.js          # 默认 DSH_BACKEND=mock，会 spawn bin/mock-dsh.js

curl -s localhost:8080/healthz
curl -s -X POST localhost:8080/v1/runs \
  -H 'content-type: application/json' \
  -d '{"run_id":"demo-1","tenant_id":"t1","task":"写一份纪要"}'

curl -sN localhost:8080/v1/runs/demo-1/events            # SSE
curl -s  "localhost:8080/v1/runs/demo-1/events?after=5"  # JSON + 游标
curl -s -X POST localhost:8080/v1/runs/demo-1/cancel -d '{"reason":"user"}'
```

Windows PowerShell 里 `curl` 是 `Invoke-WebRequest` 的别名，请用 `curl.exe`。

## 测试

```bash
node test/worker.test.js   # 6 项：loopback，沙箱内可跑
node test/spawn.test.js    # 1 项：真实 spawn；受限沙箱会 skip（EPERM）
```

当前结果：loopback 6/6 通过；spawn 1/1 通过（普通终端/CI；本机沙箱内自动 skip，
用完全权限跑过一次已确认通过）。

覆盖：健康检查、SSE 事件与 `done` 收尾、`after` 游标、取消、审批往返（批准留痕）、
`run_id` 幂等冲突。

## 接真实 DSH

前置：**Node ≥ 24.9**（DSH 的 engines 要求；本机默认 Node 20 不满足）、
`npm i -g @deepseek-ai/dsh`、`DEEPSEEK_API_KEY`。

```bash
DSH_BACKEND=sdk DSH_BIN=dsh DSH_ARGS="--profile sdk" \
DSH_PROVIDER=deepseek DSH_MODEL=deepseek-chat node src/server.js
```

先录制真实帧，再回填映射：

```bash
node probe.js --out probe-frames.jsonl --task "列出当前目录"
# 把 probe-frames.jsonl 里的 session.event.raw 形状补进 src/protocol.js，
# 并把一帧真实样本固化成测试夹具
```

## HTTP 契约

```http
POST /v1/runs
{ "run_id": "...", "tenant_id": "...", "task": "...",
  "workspace": "/workspace", "preset": "compliance@1.2.0",
  "limits": { "max_turns": 24, "wall_seconds": 900 },
  "approval_url": "https://cp.internal/v1/approvals" }

202 { "run_id": "...", "status": "queued", "events_url": "/v1/runs/.../events" }

GET  /v1/runs/{id}                 状态
GET  /v1/runs/{id}/events?after=N  JSON 或 SSE（Accept: text/event-stream）
POST /v1/runs/{id}/cancel          { "reason": "user" }
GET  /healthz
```

事件信封与 [docs/15 §4.3](../../docs/15-option-c-technical-design.md) 一致：
`event_id / tenant_id / run_id / seq / type / ts / schema_version / payload`。

## 与生产实现的差距（刻意保留）

| 项 | 本 spike | 生产 |
| --- | --- | --- |
| 事件存储 | 内存 ring | 控制面落库（Postgres + 归档） |
| 鉴权 | 无 | 控制面 mTLS + 短期 token |
| 子进程 | 每 run 一个 | 容器 + 只读根 + 网络策略 |
| 预热 | 无 | 预热池，冷启动 < 2s |
| 终止 | 杀进程组 | 容器销毁 + 卷回收 |
| session.event 映射 | 原样透传 | 按录制样本映射为领域事件 |

## S0 剩余项

- [ ] 用 `probe.js` 录制真实 `session.event` 并回填映射
- [ ] 度量 DSH 冷启动耗时与常驻内存（决定预热池大小）
- [ ] 验证审批超时 / 拒绝时 DSH 的收尾行为
- [ ] 验证 Windows 下进程树回收（`taskkill /T` 在受限环境的权限）

## 真实模型验证（需要 Key）

预检不需要 Key；冒烟需要 `DEEPSEEK_API_KEY`（或内部网关等价配置）。

```bash
# 1) 预检：Node 24 / DSH CLI / MCP 工具面 / bundle 组合
python spike/dsh-worker/scripts/real_check.py

# 2) 冒烟：真实模型 + MCP 工具 + 自动审批
DEEPSEEK_API_KEY=sk-... python spike/dsh-worker/scripts/real_check.py --run --dsh-home spike/.dsh-run
```

| 退出码 | 含义 |
| --- | --- |
| 0 | 模型确实调用了 `mcp__banking-a__*` 工具 |
| 2 | 预检通过但缺少 Key（未跑冒烟） |
| 1 | 预检或冒烟失败 |

冒烟做的事：用 `dsh --profile sdk --patch spike/dsh-bundle/cordis.patch.yml` 起 SDK runtime
（profile 建在 `--dsh-home` 指定的工作区目录，不写用户目录），worker 以 `APPROVAL_MODE=auto`
放行工具调用，然后断言事件流里出现 `mcp__banking-a__` 前缀的工具结果。

> `APPROVAL_MODE=auto` 只用于本地冒烟；生产必须走控制面审批。
