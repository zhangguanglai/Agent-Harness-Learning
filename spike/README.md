# spike — S0 验证件

S0 的目标只有一句话：**证明 DSH 可以作为服务端运行时被控制面驱动**。
五项验收见 [docs/16 §1](../docs/16-implementation-plan.md)。

| # | 验证项 | 状态 | 产物 |
| --- | --- | --- | --- |
| S0-1 | 容器内跑 `dsh-headless` | 待做（需 Docker + Node 24） | — |
| S0-2 | 自定义 bundle（工具 + 权限预设） | **已完成（组合校验）** | [dsh-bundle](dsh-bundle/) |
| S0-3 | sidecar 驱动 SDK：submit/events/cancel | **已完成（离线部分）** | [dsh-worker](dsh-worker/) |
| S0-4 | 审批外置 | **已完成（mock 往返）** | [dsh-worker](dsh-worker/) |
| S0-5 | OTel 与 token 计量 | 待做 | — |

## 已完成部分的边界

`dsh-worker` 验证的是**协议与控制面适配**：

- HTTP → NDJSON JSON-RPC → DSH 子进程 → 事件回传 → SSE；
- 审批请求外置到控制面 URL，失败关闭（无审批通道即拒绝）；
- 取消 = 杀进程组（因为 SDK 协议没有 cancel 方法）。

尚未验证的是**真实模型链路**（需要 Node ≥ 24.9 + API Key）：
`session.event` 的真实字段、冷启动耗时、DSH 进程被杀后的收尾行为。

## 下一步

1. `dsh-worker/probe.js` 录制真实帧 → 回填 `src/protocol.js`；
2. 补 S0-2 的领域 bundle（一个工具 + 一个审批预设）；
3. 用 Docker 跑 S0-1，设定 CPU/内存/时长上限；
4. 接 OTel，确认 trace 能串起 gateway → worker → tool。
