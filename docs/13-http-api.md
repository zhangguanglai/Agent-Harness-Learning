# 13 · HTTP API 与 SSE 事件流

> 对应 Phase 1 的第 ② 步：让骨架能被外部系统调用。
> 代码：[`api/server.py`](../api/server.py)、[`api/auth.py`](../api/auth.py)、
> [`api/client.py`](../api/client.py)；实验 [`lab10`](../labs/lab10_http_api.py)。

## 1. 为什么只用标准库

教学仓库引一个 Web 框架，读者就会把「框架用法」误当成「Harness 设计」。这里是
`http.server.ThreadingHTTPServer` + 手写路由，一共 300 多行，正好能看清：

- 鉴权在哪一层做（handler 入口，不是业务函数里）；
- SSE 怎么把事件按 `seq` 推给客户端；
- 取消、审批这类「控制面」请求怎么落回运行时。

生产替换成 FastAPI/Go 都可以，**接口形状不变**。

## 2. 端点

| 方法 | 路径 | 说明 | 成功码 |
| --- | --- | --- | --- |
| GET | `/health` | 健康检查（免鉴权） | 200 |
| POST | `/v1/runs` | 提交运行 | 202 |
| GET | `/v1/runs` | 本租户运行列表（最近 50） | 200 |
| GET | `/v1/runs/{id}` | 状态、结果、usage | 200 |
| GET | `/v1/runs/{id}/events?after=N` | 事件 JSON | 200 |
| GET | `/v1/runs/{id}/events`（`Accept: text/event-stream`） | SSE 流 | 200 |
| POST | `/v1/runs/{id}/cancel` | 取消（协作式） | 202 |
| GET | `/v1/approvals` | 本租户待审批 | 200 |
| POST | `/v1/approvals/{id}` | 批准/拒绝 | 200 |
| GET | `/v1/sessions/{id}/history` | 事件投影出的历史 | 200 |

错误码：`400` 参数错、`401` 凭证无效、`403` 缺 scope、`404` 不存在**或跨租户**、
`413` 请求体过大、`429` 限流/并发超限、`500` 内部错误（不回栈）。

## 3. 鉴权：角色只来自 token

```
Authorization: Bearer t-alice
```

服务端把 token 映射成 `Principal(tenant, user, roles, scopes)`。
**请求体里的 `roles` 字段会被忽略**——这是最小权限的底线：客户端不能自我提权。

```python
tokens = TokenRegistry.from_specs(["t-alice=acme:alice:operator", "t-bob=beta:bob:viewer"])
```

生产替换点：把 `TokenRegistry.authenticate` 换成 OIDC/JWT 校验，其余不变。

## 4. 租户隔离：两道锁

1. **数据面**：`require_run()` 校验 `handle.request.tenant == principal.tenant`，
   不一致返回 **404 而不是 403**（不泄露「这个 run 存在」）。
2. **文件面**：工作区由服务端拼 `workspace_root/<tenant>/<session_id>`，
   并做 `is_relative_to` 校验；客户端只能提供 session 名，不能提供路径。

历史查询同理：只投影本租户 run 的事件。

## 5. 事件流：`seq` 是游标

```bash
curl -N -H "Authorization: Bearer t-alice" \
     -H "Accept: text/event-stream" \
     http://127.0.0.1:8765/v1/runs/<id>/events?after=0
```

```
retry: 1000

id: 1
data: {"seq":1,"kind":"run_queued",...}

id: 7
data: {"seq":7,"kind":"tool_result",...}

event: done
data: {"status":"done"}
```

设计要点：

- **`after` 断点续传**：网络断了，客户端带最后一个 `seq` 重连即可，服务端不需要会话；
- **`event: done` 明确收尾**：客户端不必靠超时判断结束；
- **心跳**：10 秒无事件发 `: ping`，避免中间代理掐连接；
- **`Connection: close`**：SSE 是长连接，结束即关，不玩 chunked 复杂度。

## 6. 控制面：取消与审批

```bash
# 取消
curl -X POST -H "Authorization: Bearer t-alice" -d '{"reason":"user"}' \
     http://127.0.0.1:8765/v1/runs/<id>/cancel

# 审批
curl -H "Authorization: Bearer t-alice" http://127.0.0.1:8765/v1/approvals
curl -X POST -H "Authorization: Bearer t-alice" \
     -d '{"approved":true,"by":"alice"}' \
     http://127.0.0.1:8765/v1/approvals/<approval_id>
```

取消是**协作式**的：置 `cancel_event` → 当前轮次结束后停止 → 工具进程组被杀。
所以 `cancel` 返回 202 表示「已受理」，终态要看 `status` 或 `run_cancelled` 事件。

## 7. 配额与限流

| 闸门 | 位置 | 行为 |
| --- | --- | --- |
| 令牌桶（按 token） | `RateLimiter` | 超限 429 + `retry_after_s` |
| 租户并发 | `max_concurrent_per_tenant` | 超限 429，提示先等已有 run 结束 |
| 请求体大小 | `max_body_bytes`（默认 1MB） | 413 |

限流放在**鉴权之后**：未认证请求不该消耗配额，也不该泄露限流状态。

## 8. 跑起来

```bash
python -m api --port 8765 --token "dev-token=demo:alice:operator"
# 带持久化事件库
python -m api --db .harness/events.db --workspace-root .harness/workspaces
```

Python 客户端：

```python
from api import HarnessClient

client = HarnessClient("http://127.0.0.1:8765", "dev-token")
run = client.submit("看看工作区并写 NOTES.md", "s1", mode="acceptEdits")
for event in client.stream(run["run_id"]):
    print(event["kind"])
print(client.wait(run["run_id"])["status"])
```

## 9. 已知限制（生产前必须补）

| 限制 | 生产做法 |
| --- | --- |
| 单进程、无 TLS | 反向代理终止 TLS；多副本需要共享事件总线 |
| 审批 broker 在内存 | 落库 + 审批 Webhook/IM 通知 + 双人审批 |
| SSE 轮询存储 | 事件总线（Redis Streams/Kafka）推送到 API 副本 |
| 无 OpenAPI | 由 dataclass 生成 schema，或换 FastAPI |
| 无审计导出 | 事件表按租户分区 + 导出任务 |
| 无请求追踪 ID | 注入 `traceparent`，与 OTel 打通 |

## 10. 自测

1. 为什么跨租户访问返回 404 而不是 403？
2. 为什么角色不能从请求体读取？如果允许会发生什么？
3. SSE 的 `after` 参数与 `Last-Event-ID` 的语义差别是什么？你更倾向哪个？
4. 取消为什么返回 202 而不是 200？客户端如何知道「真的停了」？
5. 多副本部署时，SSE 为什么不能只读本地内存？需要什么基础设施？
