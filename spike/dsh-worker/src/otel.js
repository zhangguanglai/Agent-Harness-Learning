/**
 * OTLP/HTTP JSON 追踪（Node 侧，零依赖）。
 *
 * 与 Python 侧 observability/otel.py 共用一套语义：
 * - W3C traceparent 传播（编排层 → worker）；
 * - span 立即导出到 `${endpoint}/v1/traces`，失败静默（观测不能影响业务）。
 */
import { randomBytes } from 'node:crypto';

export function parseTraceparent(value) {
  if (!value || typeof value !== 'string') return null;
  const parts = value.split('-');
  if (parts.length !== 4 || parts[0] !== '00') return null;
  if (parts[1].length !== 32 || parts[2].length !== 16) return null;
  return { traceId: parts[1], parentSpanId: parts[2] };
}

export class Tracer {
  constructor({ endpoint = process.env.OTEL_EXPORTER_OTLP_ENDPOINT, service = 'dsh-worker', attributes = {} } = {}) {
    this.endpoint = (endpoint || '').replace(/\/+$/, '');
    this.service = service;
    this.attributes = attributes;
    this.exported = 0;
    this.failed = 0;
  }

  get enabled() {
    return Boolean(this.endpoint);
  }

  start(name, { traceparent, attributes = {} } = {}) {
    const parent = parseTraceparent(traceparent);
    return {
      traceId: parent?.traceId ?? randomBytes(16).toString('hex'),
      spanId: randomBytes(8).toString('hex'),
      parentSpanId: parent?.parentSpanId ?? '',
      name,
      startNs: String(Date.now() * 1_000_000),
      attributes: { ...this.attributes, ...attributes },
      ended: false,
    };
  }

  end(span, { error } = {}) {
    if (!span || span.ended) return;
    span.ended = true;
    span.endNs = String(Date.now() * 1_000_000);
    span.status = error ? 2 : 1;
    if (error) span.attributes.error = String(error).slice(0, 300);
    this.export(span);
  }

  export(span) {
    if (!this.enabled) return;
    const payload = {
      resourceSpans: [
        {
          resource: { attributes: [{ key: 'service.name', value: { stringValue: this.service } }] },
          scopeSpans: [
            {
              scope: { name: 'mini-harness', version: '0.1.0' },
              spans: [
                {
                  traceId: span.traceId,
                  spanId: span.spanId,
                  parentSpanId: span.parentSpanId,
                  name: span.name,
                  kind: 1,
                  startTimeUnixNano: span.startNs,
                  endTimeUnixNano: span.endNs,
                  attributes: toAttributes(span.attributes),
                  status: { code: span.status },
                },
              ],
            },
          ],
        },
      ],
    };
    fetch(`${this.endpoint}/v1/traces`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(2000),
    })
      .then(() => {
        this.exported += 1;
      })
      .catch(() => {
        this.failed += 1;
      });
  }
}

function toAttributes(attributes) {
  return Object.entries(attributes).map(([key, value]) => {
    if (typeof value === 'boolean') return { key, value: { boolValue: value } };
    if (typeof value === 'number') return { key, value: { intValue: String(Math.trunc(value)) } };
    return { key, value: { stringValue: String(value) } };
  });
}
