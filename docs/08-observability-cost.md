# 08 · 可观测与成本：先看见，再优化

> 对应站点：[Token 成本与会话经济学](https://agent.jeffjade.com/claude-code/token-economics/)、
> [调试与错误恢复](https://agent.jeffjade.com/claude-code/debug-error-recovery/)、
> [排障速查](https://agent.jeffjade.com/claude-code/troubleshooting-faq/)。
> 代码：[`trace.py`](../mini_harness/trace.py)、实验 [`lab07`](../labs/lab07_trace_cost.py)。

## 1. 为什么先做轨迹，再做功能

Agent 的 bug 大多是**过程性**的：不是「结果错」，而是「在第 7 轮开始重复读同一个文件」。
没有轨迹，你只能看到最终答案和一张账单。

本仓库的事件模型（`Tracer.emit`）：

| 事件 | 关键字段 | 回答什么问题 |
| --- | --- | --- |
| `session_start` | system_hash、mode、tools | 这次运行带着什么配置 |
| `turn` | turn、messages | 上下文规模怎么长 |
| `llm_response` | input/output tokens、tool_calls | 每次推理花了多少 |
| `permission` | tool、action、reason | 谁被拦、为什么 |
| `tool_call` / `tool_result` | args、ok、chars、duration_ms | 工具做了什么、多慢 |
| `compaction` | before/after tokens、dropped | 什么被丢了 |
| `subagent_start` / `end` | goal、turns、tokens | 子任务花了多少 |
| `session_end` | stopped、turns、usage | 为什么停 |

每条记录都带 `ts`、`session`、`seq`，可直接回放。

## 2. token 账本：只认 provider 的 usage

```python
usage = Usage(input_tokens=usage_raw["prompt_tokens"], output_tokens=usage_raw["completion_tokens"])
```

不要用「字数 / 4」估算来计费——那是给预算判断用的（`estimate_tokens`），不是账单。
本仓库两者分开：

- `estimate_messages_tokens`：离线估算，用于压缩触发；
- `Usage`：模型返回的真实用量，用于成本。

实验 `lab07` 断言「轨迹里 llm_response 的 token 之和 == tracer.usage」，
这就是账本一致性的最小测试。

## 3. 成本结构：钱花在哪

一次任务的成本 ≈ Σ(每轮输入 token × 输入单价 + 输出 token × 输出单价)。

关键观察：**输入 token 随轮数近似线性增长，输出增长慢**。所以：

| 成本来源 | 占比（典型） | 优化手段 |
| --- | --- | --- |
| 历史对话重复发送 | 最大 | 压缩、裁剪工具输出、子代理隔离 |
| 系统提示 | 固定 | 前缀缓存、记忆冻结、技能按需 |
| 工具 schema | 固定 | toolset 收窄 |
| 输出 | 较小 | 提示「不要复述文件」 |
| 重试/失败 | 可变 | 工具错误进对话、减少无效轮次 |

**最贵的不是输出，是「每一轮都重新发送全部历史」。**

## 4. 缓存：省钱的确定性手段

前缀缓存命中时，输入 token 的计费通常大幅下降（各家折扣不同，但方向一致）。
命中条件：**前缀字节完全相同**。

工程上要保证：

1. 系统提示只在会话开始时构建（本仓库 `run()` 里一次）；
2. 记忆冻结快照，不每轮刷新；
3. 工具 schema 顺序稳定（不要用 `set` 迭代生成）；
4. 不在前缀里塞时间戳、随机 id、动态统计。

本仓库把 `system_hash` 写进轨迹，就是为了**验证**「同一会话内前缀没变」。

## 5. 常见成本事故与归因

| 事故 | 症状 | 轨迹里怎么发现 | 修法 |
| --- | --- | --- | --- |
| 死循环读文件 | 账单暴涨、轮次多 | 同一 `tool+args` 重复出现 | 停止条件 + 提示「不要重复失败调用」 |
| 上下文不压缩 | 每轮输入线性涨到上限 | `turn` 的 messages 数持续涨、没有 compaction | 调低 `compact_at` |
| 工具输出过大 | 单轮 token 尖峰 | `tool_result.chars` 很大 | 截断 + 工具内限制 |
| 子代理风暴 | 并发高、总量翻倍 | 多个 subagent_start | 并发上限 + 更严的派发判据 |
| 审批轰炸 | 人受不了，开 bypass | permission 里大量 ask | 加 allow 前缀规则 |
| 模型选错 | 简单任务用贵模型 | 按 session 统计 | 任务分级 + fallback 链 |

## 6. 评测：别只看「感觉变好了」

最小可用的评测三件套：

1. **固定任务集**：10–30 个有明确验收的任务（跑测试、修 lint、生成文档）；
2. **固定判据**：命令退出码 / diff 是否只改目标文件 / 关键词；
3. **每次改动跑一遍**：记录通过率、平均轮次、平均 token。

指标比单看通过率更重要：

| 指标 | 说明 | 变差的信号 |
| --- | --- | --- |
| 通过率 | 验收通过比例 | 提示词改动引入回归 |
| 平均轮次 | 效率 | 循环变啰嗦 |
| 平均输入 token | 成本 | 上下文管理退化 |
| 拒绝率 | 权限摩擦 | 规则太严或任务越界 |
| 压缩次数 | 上下文压力 | 任务变复杂或窗口太小 |

本仓库的 `labs/verify.py` 是最小版本：7 个实验 + demo，全绿才算通过。
真实项目把任务集换成「仓库里的真实工单」即可。

## 7. 模型路由与 fallback

- **分级**：简单任务用小模型，复杂规划用大模型；
- **fallback 链**：主模型 429/5xx 时按顺序切换（Hermes 有 `fallback_providers`）；
- **辅助模型**：压缩摘要、标题生成等用便宜模型；
- **注意**：不同模型的 tool_call 格式可能不同，切换要在边界处转换。

站点在 [架构拆解](https://agent.jeffjade.com/hermes-agent/architecture-deep-dive/) 里
把「主模型 fallback」和「auxiliary 任务 fallback」分开配置，这个区分很实用。

## 8. 日志与隐私

- 轨迹里可能有文件内容、命令输出、密钥（如果被写进文件）——**按敏感数据管理**；
- 生产环境做脱敏 Hook（本仓库 `secret_guard` 是写入侧，读取侧同理）；
- 保留期限与访问控制要和公司合规对齐；
- 不要默认把轨迹上传到第三方。

## 9. 自测

1. 为什么「输入 token」是成本大头？它和轮数是什么关系？
2. 前缀缓存命中的条件是什么？列三种会破坏它的写法。
3. 轨迹里出现 20 次相同 `tool+args`，你会先改提示词还是改循环？为什么？
4. 通过率提升了但平均轮次翻倍、token 翻三倍，这个改动该上线吗？
5. 如果只能加一个监控指标来发现「Agent 变笨」，你加哪个？
