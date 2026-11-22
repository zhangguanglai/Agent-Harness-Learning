# 12 · Phase 0 运行时骨架：已落地的六个 PR

> 规划见 [11 生产化](11-production-harness.md)。本文说明**代码实现了什么、为什么这样切、
> 哪些是刻意留白的**，以及如何运行与验收。
> 代码：[`runtime/`](../runtime/)、[`bench/`](../bench/)、实验 [`lab08`](../labs/lab08_runtime.py)、
> [`lab09`](../labs/lab09_gateway_policy.py)。

## 1. 六个 PR → 落地文件

| # | PR | 交付 | 关键文件 |
| --- | --- | --- | --- |
| 1 | Runtime：submit / cancel / events | `Runtime`、`RunHandle`、`RunStatus` 状态机 | [runtime/runtime.py](../runtime/runtime.py) |
| 2 | 会话事件溯源 | `Event`、`SessionStore`（内存 / SQLite）、投影 | [runtime/events.py](../runtime/events.py)、[runtime/store.py](../runtime/store.py) |
| 3 | 工具网关 | schema 版本、受控重试、错误码统计 | [runtime/gateway.py](../runtime/gateway.py) |
| 4 | 沙箱执行器 | `Sandbox` 协议 + `LocalSandbox`（进程组/Job Object、超时、取消、env 过滤） | [mini_harness/sandbox.py](../mini_harness/sandbox.py) |
| 5 | 主体权限 + 审批状态机 | `PolicyContext`、角色能力表、`waiting_approval`、批准人留痕 | [runtime/policy.py](../runtime/policy.py)、[runtime/approvals.py](../runtime/approvals.py) |
| 6 | 任务级评测门禁 | 6 个任务、指标表、JSON 输出、CI 退出码 | [bench/run.py](../bench/run.py)、[bench/tasks.py](../bench/tasks.py) |

同时给内核补了三处**向后兼容**的钩子（不改变原有行为）：

- `Agent(cancel_event=, sandbox=, policy=, tool_executor=)`：协作式取消、注入沙箱/策略/执行器；
- `ToolResult.error_code / retryable / meta`：错误分类与重试决策；
- `mini_harness/errors.py`：`ToolError` 体系 + `classify()`。

## 2. 分层

```
runtime/                     生产外壳（无状态、可重启、可观测）
  runtime.py      Runtime / RunHandle / RunStatus   编排与生命周期
  store.py        EventStore + projection            事实来源
  gateway.py      ToolGateway                        执行入口 + 重试
  policy.py       TenantPermissionPolicy             主体 + 角色
  approvals.py    ApprovalBroker                     审批状态机
  events.py       Event                              事件模型

mini_harness/                教学内核（循环、工具协议、上下文、记忆）
  loop.py         Agent                              不变，只加了钩子
  sandbox.py      LocalSandbox                       进程边界
  tools.py        registry + bash 走 sandbox
  errors.py       错误分类
```

原则：**内核不知道存储、审批、租户；外壳不知道提示词怎么拼。** 两边通过
`Agent` 的 `on_event`、`tool_executor`、`approver`、`policy` 四个接缝连接。

## 3. 关键接口

### 3.1 提交与状态

```python
rt = Runtime(InMemoryStore(), workers=2, broker=ApprovalBroker(auto_approve=True))
handle = rt.submit(RunRequest(task="...", session_id="s1", workspace=Path("."), roles=("operator",)))
handle.status          # queued | running | waiting_approval | done | failed | cancelled
handle.wait(30)        # 阻塞到终态
handle.cancel("user")  # 协作式取消：模型轮次之间 + 工具进程组
handle.events()        # 该 run 的全部事件
```

`waiting_approval` 是显式状态：监控能看到「卡在等人」，而不是「在跑但很慢」。

### 3.2 事件与投影

| 事件 | 产生点 | 投影成 |
| --- | --- | --- |
| `run_queued` / `run_started` | 提交、开始执行 | user 消息（任务） |
| `turn_started` | 每轮开始 | — |
| `model_response` | 模型返回 | assistant 消息（含 tool_calls） |
| `permission_decision` | 权限判定 | — |
| `approval_requested` / `approval_resolved` | 等待/批准 | — |
| `tool_call` / `tool_result` | 工具执行 | tool 消息 |
| `compaction` | 上下文压缩 | — |
| `run_finished` / `run_cancelled` / `run_failed` | 终态 | — |

`project_messages()` 只认三类（任务、助手、工具），所以「恢复」= 重放事件，不需要额外状态。

### 3.3 网关的受控重试

```python
gateway.invoke(call, ctx)
# 只重试：可重试错误码（TIMEOUT/RATE_LIMITED/NETWORK） + 只读工具
# 有副作用的工具即使可重试也不自动重试，meta["retry_suppressed"]="side_effect_tool"
```

这是「重试」与「幂等」之间最小的一条工程红线：**没有幂等键，就别自动重试写操作。**

### 3.4 角色与审批

```python
capabilities_for(("viewer",))       # 只读工具集合
TenantPermissionPolicy(ctx=..., mode=..., allow=..., deny=..., ask=...)
approver  # Runtime 内置：ask → 创建审批请求 → 阻塞等待 → 记录批准人
```

审批等待超时按**拒绝**处理（fail closed）；批准人写入 `approval_resolved.by`，用于审计。

### 3.5 沙箱

```python
LocalSandbox().exec(command, cwd=ws, limits=SandboxLimits(timeout_s=120), cancel_event=ev)
# → ExecResult(exit_code, stdout, stderr, timed_out, cancelled, truncated)
```

- POSIX：`start_new_session=True` + `os.killpg`；
- Windows：Job Object（`KILL_ON_JOB_CLOSE` + `TerminateJobObject`），因为受限环境里
  `taskkill` 可能返回 Access denied；
- 环境变量默认剔除 `*_KEY` / `*_TOKEN` / 云凭证；
- 输出上限保留末尾（错误通常在最后）。

## 4. 运行与验收

```bash
python labs/lab08_runtime.py          # 事件溯源 / 恢复 / 取消
python labs/lab09_gateway_policy.py   # 网关重试 / 审批状态机 / 角色 / bench
python bench/run.py                   # 6 个任务级评测，输出指标表
python bench/run.py --min-pass-rate 0.8   # CI 门禁（退出码）
python labs/verify.py                 # 全部：9 个实验 + demo + bench
```

`verify.py` 现在把 bench 也纳入门禁：通过率低于 80% 直接失败。

真实模型接入（替换脚本模型）：

```python
from mini_harness import OpenAICompatLLM
llm = OpenAICompatLLM(model="deepseek-chat", base_url="https://api.deepseek.com/v1")
rt.submit(RunRequest(..., llm=llm))
```

## 5. 与生产（Phase 1）还差什么

| 缺口 | 现状 | Phase 1 做法 |
| --- | --- | --- |
| 执行隔离 | 本机进程 + Job Object | 每 run 一个容器/微虚机，只读根 + 网络白名单 |
| 存储 | SQLite 单机 | Postgres + 分区 + 归档；事件表按月分区 |
| 调度 | 进程内线程池 | 队列（SQS/Redis）+ 独立 worker 部署 + 背压 |
| API | 直接调 Python | 标准库 HTTP/SSE 已实现（见 [13 HTTP API](13-http-api.md)）；生产需 TLS、多副本与事件总线 |
| 鉴权 | 调用方传 tenant/user | OIDC/JWT → 主体；服务账号与 scope |
| 配额 | 无 | per-tenant 并发、token、时长三类配额 |
| 审批 | 内存 broker | 持久化审批表 + Webhook/IM 通知 + 双人审批 |
| 模型路由 | 单 LLM | 路由 + fallback 链 + 缓存 + 预算熔断 |
| 观测 | 事件 + 本地表 | OpenTelemetry + 指标 + SLO 告警 + 采样 |
| 版本化 | 无 | 提示/模型/工具/策略的版本号 + 灰度 |

## 6. 已知限制（刻意保留，避免误导）

1. **取消不能打断模型调用**：`LLM.complete` 是阻塞的；真实实现需要流式 + 关闭连接。
2. **单机沙箱不是安全边界**：它只提供进程组与超时；文件系统/网络隔离必须靠容器。
3. **事件没有压缩策略**：长会话事件会无限增长，生产要定期快照 + 归档。
4. **审批 broker 不持久化**：进程重启会丢 pending（生产要落库）。
5. **bench 用脚本模型**：测的是「运行时是否正确」，不是「模型是否聪明」。

## 7. 自测

1. 「恢复」为什么不需要单独的恢复代码？事件模型里哪三类事件支撑了它？
2. 为什么有副作用的工具不能自动重试？什么条件下才可以？
3. `waiting_approval` 为什么要单独建模，而不是让 `approver` 阻塞在工具线程里？
4. Windows 上为什么放弃 `taskkill` 改用 Job Object？两者的差别是什么？
5. 如果明天要把 `Runtime` 改成 HTTP 服务，哪几个接口会暴露出去？哪些必须留在进程内？
