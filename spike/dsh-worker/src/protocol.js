/**
 * 事件信封与 DSH 通知的归一化。
 *
 * 信封是我们自己定的契约（控制面消费），DSH 的原始通知是上游契约：
 * 这一层的唯一职责是「把上游形状隔离在这里」，上游改版只改这个文件。
 *
 * ⚠️ S0 待验证：`session.event` 的 payload 具体字段来自 dsh-session 的 SessionEvent，
 * 本仓库尚未用真实模型跑通录制（需要 Node ≥ 24.9 + API Key）。当前做法是
 * **原样透传 raw**，并把 `normalize` 保持为纯函数，等 probe 录到真实帧后回填映射。
 */
import { randomUUID } from 'node:crypto';

export const SCHEMA_VERSION = 1;

export const TERMINAL_TYPES = new Set(['run.succeeded', 'run.failed', 'run.cancelled', 'run.timed_out']);

export function makeEvent({ runId, tenantId, seq, type, payload = {} }) {
  return {
    event_id: randomUUID(),
    tenant_id: tenantId,
    run_id: runId,
    seq,
    type,
    ts: new Date().toISOString(),
    schema_version: SCHEMA_VERSION,
    payload,
  };
}

/**
 * 把 SDK 通知映射为 1 个或多个内部事件。
 * 未知通知统一落到 `dsh.notification`，保证前向兼容（不丢数据）。
 */
export function normalizeNotification(method, params = {}) {
  switch (method) {
    case 'session.event': {
      const raw = params.event ?? null;
      const rawType = raw?.type ?? null;
      const data = raw?.data ?? {};
      const payload = {
        session_id: params.sessionId,
        raw_type: rawType,
        event: raw,
      };
      // 真实 DSH 把工具调用放在 session.event 的 tool/call 里（已用 0.2.2-rc.5 实测）
      if (rawType === 'tool/call') {
        payload.tool = data.name ?? null;
        payload.tool_args = data.arguments ?? null;
        payload.call_id = data.callId ?? null;
      }
      if (rawType === 'assistant/message') {
        payload.text = extractText(raw);
      }
      return [{ type: 'session.event', payload }];
    }
    case 'session.status':
      return [{ type: 'run.status', payload: { status: params.status } }];
    case 'session.query.completed':
      return [
        {
          type: 'run.succeeded',
          payload: {
            final_response: params.finalResponse ?? '',
            finish_reason: params.finishReason ?? 'unknown',
            command_id: params.commandId ?? null,
          },
        },
      ];
    case 'subagent.started':
      return [{ type: 'subagent.started', payload: params }];
    case 'subagent.finished':
      return [{ type: 'subagent.finished', payload: params }];
    default:
      return [{ type: 'dsh.notification', payload: { method, params } }];
  }
}

/** 从 session.event 的 raw 里尽量提取可读文本，仅用于 demo 输出。 */
export function extractText(rawEvent) {
  if (!rawEvent || typeof rawEvent !== 'object') return '';
  if (typeof rawEvent.text === 'string') return rawEvent.text;
  if (typeof rawEvent.delta === 'string') return rawEvent.delta;
  const data = rawEvent.data ?? rawEvent;
  const blocks = data.message?.content ?? data.content ?? rawEvent.message?.content ?? rawEvent.content;
  if (Array.isArray(blocks)) {
    return blocks
      .map((block) => (typeof block === 'string' ? block : block?.text ?? ''))
      .join('')
      .trim();
  }
  return '';
}
