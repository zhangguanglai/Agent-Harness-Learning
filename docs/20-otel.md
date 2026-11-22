# 20 · S0-5：用 OTLP 追踪把一次评估串起来

> 目标：一次任务里「谁调了谁、各花多久、花了多少 token」可查询，而不是翻日志。
> 代码：[observability/otel.py](../observability/otel.py)、[spike/dsh-worker/src/otel.js](../spike/dsh-worker/src/otel.js)、
> [deploy/otel/collector.py](../deploy/otel/collector.py)、实验 [lab15](../labs/lab15_otel.py)。

## 1. 为什么手写 OTLP

- 仓库零依赖，S0 只要「能看见链路」，不需要完整 SDK；
- OTLP/HTTP JSON 是公开协议，手写 exporter 反而看得清 span 结构；
- 生产换官方 SDK 时，调用点（`start` / `end`）不变。

## 2. span 树

```
assessment.run（orchestrator）
 ├─ agent.run（orchestrator → worker）
 │   └─ worker.run（dsh-worker）
 │       ├─ worker.tool（tool/call…tool/result 之间）
 │       └─ worker.tool …
 └─ approval.wait（orchestrator，需要审批时）
```

属性（节选）：

| span | 属性 |
| --- | --- |
| `assessment.run` | `run.id`、`case.id`、`tenant.id`、`run.status` |
| `agent.run` | `run.id`、`worker`、`agent.status` |
| `worker.run` | `run.id`、`run.status`、`llm.input_tokens`、`llm.output_tokens` |
| `worker.tool` | `tool.name`、`run.id` |
| `approval.wait` | `approval.action`、`approval.approved`、`approval.by` |

## 3. 传播：W3C traceparent

```
orchestrator: trace_id = random 16B
assessment.run.span_id
        │  traceparent = 00-<trace>-<assessment.run.span>-01
        ▼
agent.run（子 span）→ 提交 worker 时携带 00-<trace>-<agent.run.span>-01
        ▼
worker.run（parent = agent.run）→ worker.tool（parent = worker.run）
```

关键点：**trace 上下文随业务请求传递**，不是从日志里拼出来的。worker 把
`traceparent` 解析成自己的 parent span，于是跨进程的父子关系是真实的。

## 4. 收集器

```bash
python -m deploy.otel.collector --port 4318
curl -s localhost:4318/spans | python -m json.tool | head
```

- `POST /v1/traces`：接收 OTLP/HTTP JSON，按 trace 存；
- `GET /spans`：调试用；
- `DELETE /spans`：清空；
- 失败静默：**观测不可用不能影响业务**（lab15 专门验证了这一点）。

## 5. 实测结果（lab15）

```
✓ 链路：assessment.run → agent.run → worker.run → worker.tool（同一 trace）
✓ 计量：worker.run token 输入=360，状态=succeeded
✓ 工具 span：tool.name=kb.search
✓ 韧性：collector 不可达时评估仍完成
```

## 6. 与 DSH 自带 OTel 的关系

DSH 有 `dsh-otel` / `dsh-session-telemetry-otel` 插件，可按 profile 挂载并导出到同一
collector。本 S0 先做「控制面 + worker」两层，原因是：

1. 这两层决定了用户感知的时延与失败归因；
2. DSH 的 span 是模型/工具内部细节，接进来只需在 bundle patch 里加两行（S1 再做）。

接入方式（未验证，作为下一步）：

```yaml
- id: otel
  config:
    endpoint: !!js process.env.OTEL_EXPORTER_OTLP_ENDPOINT
```

## 7. 已知限制

| 限制 | 影响 | 下一步 |
| --- | --- | --- |
| 单 span 同步导出（无批量） | 高并发下开销大 | 批量 + 采样 |
| 无 metrics/logs | 只有 trace | OTel metrics + 结构化日志 |
| 无采样 | 全量上报 | 头部采样 + 错误全采 |
| 属性是字符串 | 查询要转换 | 语义约定（`gen_ai.*`） |
| collector 无持久化 | 重启丢失 | Jaeger/Tempo |

## 8. 自测

1. 为什么 `agent.run` 要在提交 worker **之前** 创建？晚一步会怎样？
2. `traceparent` 里为什么带 `-01`？采样位在分布式追踪里起什么作用？
3. 为什么观测失败必须静默？什么情况下应该反过来（阻断业务）？
4. 如果把 token 计量放在 worker 而不是编排层，对成本归因有什么影响？
5. 采样率 10% 时，如何保证「失败的请求一定被记录」？
