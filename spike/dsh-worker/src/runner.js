/**
 * RunManager：一个 worker 进程内的运行管理器。
 *
 * 职责边界（与控制面分工）：
 * - 只负责「启动子进程、转发事件、处理审批往返、取消」；
 * - 不持久化事件（控制面订阅 SSE 后落库）；
 * - 不做配额/计费/鉴权（控制面的事）。
 */
import { makeEvent, normalizeNotification, TERMINAL_TYPES, extractText } from './protocol.js';

const TERMINAL_STATUS = {
  'run.succeeded': 'succeeded',
  'run.failed': 'failed',
  'run.cancelled': 'cancelled',
  'run.timed_out': 'timed_out',
};

export class RunManager {
  constructor({
    backendFactory,
    approvalClient,
    maxWallMs = Number(process.env.MAX_WALL_MS ?? 15 * 60 * 1000),
    maxEvents = 20_000,
  }) {
    this.backendFactory = backendFactory;
    this.approvalClient = approvalClient ?? defaultApprovalClient;
    this.maxWallMs = maxWallMs;
    this.maxEvents = maxEvents;
    this.runs = new Map();
    this.startedAt = Date.now();
  }

  create({ runId, tenantId, task, workspace, preset, limits, approvalUrl }) {
    if (!runId) throw badRequest('run_id is required');
    if (!task) throw badRequest('task is required');
    if (this.runs.has(runId)) throw conflict(`run ${runId} already exists`);

    const run = {
      id: runId,
      tenantId: tenantId ?? 'default',
      task,
      workspace,
      preset: preset ?? null,
      limits: limits ?? {},
      approvalUrl: approvalUrl ?? process.env.APPROVAL_URL ?? null,
      status: 'queued',
      seq: 0,
      events: [],
      subscribers: new Set(),
      closed: false,
      backend: null,
      lastText: '',
      createdAt: Date.now(),
      wallTimer: null,
    };
    this.runs.set(runId, run);
    this._emit(run, 'run.queued', { task, preset: run.preset });
    setImmediate(() => this._start(run));
    return run;
  }

  get(runId) {
    return this.runs.get(runId);
  }

  events(runId, after = 0) {
    const run = this.runs.get(runId);
    if (!run) return null;
    return run.events.filter((event) => event.seq > after);
  }

  subscribe(runId, after, listener) {
    const run = this.runs.get(runId);
    if (!run) return null;
    for (const event of run.events.filter((e) => e.seq > after)) listener(event);
    if (run.closed) return () => {};
    run.subscribers.add(listener);
    return () => run.subscribers.delete(listener);
  }

  cancel(runId, reason = 'user') {
    const run = this.runs.get(runId);
    if (!run || run.closed) return null;
    if (run.status === 'cancelling') return run;
    run.status = 'cancelling';
    const type = reason === 'timed_out' ? 'run.timed_out' : 'run.cancelled';
    this._emit(run, 'run.cancelling', { reason });
    Promise.resolve(run.backend?.cancel?.())
      .catch(() => {})
      .finally(() => this._finish(run, type, { reason }));
    return run;
  }

  async shutdown() {
    for (const run of this.runs.values()) {
      await run.backend?.stop?.().catch(() => {});
    }
  }

  // -- 内部 -------------------------------------------------------------- //
  async _start(run) {
    run.status = 'running';
    this._emit(run, 'run.started', { backend: this.backendFactory.name ?? 'unknown', workspace: run.workspace });
    run.wallTimer = setTimeout(() => this.cancel(run.id, 'timed_out'), this.maxWallMs);
    run.wallTimer.unref?.();

    try {
      run.backend = this.backendFactory({
        run,
        onEvent: (method, params) => this._onNotification(run, method, params),
        onApproval: (approval) => this._onApproval(run, approval),
        onExit: (code, signal) => this._onExit(run, code, signal),
        onStderr: (line) => this._emit(run, 'worker.stderr', { line: line.slice(0, 2000) }),
      });
      await run.backend.start({ task: run.task, sessionId: run.id });
    } catch (err) {
      this._finish(run, 'run.failed', { error: err?.message ?? String(err) });
    }
  }

  _onNotification(run, method, params) {
    if (run.closed) return;
    for (const event of normalizeNotification(method, params)) {
      if (event.type === 'run.succeeded') {
        this._finish(run, 'run.succeeded', {
          ...event.payload,
          final_response: event.payload.final_response || run.lastText,
        });
        return;
      }
      if (event.type === 'run.status') {
        this._emit(run, 'run.status', event.payload);
        // 某些 composition 只发 idle；用它兜底收尾
        if (params.status === 'idle' && !run.closed) {
          this._finish(run, 'run.succeeded', { final_response: run.lastText, finish_reason: 'idle' });
        }
        continue;
      }
      if (event.type === 'session.event') {
        const text = extractText(event.payload.event);
        if (text) run.lastText = text;
      }
      this._emit(run, event.type, event.payload);
    }
  }

  async _onApproval(run, approval) {
    run.status = 'waiting_approval';
    this._emit(run, 'approval.requested', {
      tool: approval.tool,
      call_id: approval.callId ?? null,
      reason: approval.reason ?? '',
    });
    let decision;
    try {
      decision = await this.approvalClient({ run, approval });
    } catch (err) {
      decision = { approved: false, by: 'approval-error', error: err?.message ?? String(err) };
    }
    this._emit(run, 'approval.resolved', {
      approved: Boolean(decision?.approved),
      by: decision?.by ?? '',
      error: decision?.error ?? '',
    });
    run.status = 'running';
    return decision?.approved ? 'allowed-once' : 'rejected';
  }

  _onExit(run, code, signal) {
    if (run.closed) return;
    if (run.status === 'cancelling') {
      this._finish(run, 'run.cancelled', { reason: 'process-exit', exit_code: code, signal });
      return;
    }
    this._finish(run, code === 0 ? 'run.succeeded' : 'run.failed', {
      exit_code: code,
      signal: signal ?? null,
      final_response: run.lastText,
    });
  }

  _emit(run, type, payload) {
    if (run.closed) return null;
    const event = makeEvent({ runId: run.id, tenantId: run.tenantId, seq: run.seq + 1, type, payload });
    run.seq = event.seq;
    run.events.push(event);
    if (run.events.length > this.maxEvents) run.events.splice(0, run.events.length - this.maxEvents);
    for (const listener of run.subscribers) {
      try {
        listener(event);
      } catch {
        /* 单个订阅者异常不影响其他订阅者 */
      }
    }
    return event;
  }

  _finish(run, type, payload) {
    if (run.closed) return;
    if (!TERMINAL_TYPES.has(type)) throw new Error(`not a terminal event: ${type}`);
    clearTimeout(run.wallTimer);
    run.status = TERMINAL_STATUS[type];
    const event = this._emit(run, type, payload);
    run.closed = true;
    run.subscribers.clear();
    // 终态后回收 DSH 子进程：否则 worker 进程会被子进程句柄拖住
    Promise.resolve(run.backend?.stop?.()).catch(() => {});
    return event;
  }
}

async function defaultApprovalClient({ run, approval }) {
  if (!run.approvalUrl) {
    // 失败关闭：没有审批通道就不放行
    return { approved: false, by: 'no-approver' };
  }
  const response = await fetch(run.approvalUrl, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      run_id: run.id,
      tenant_id: run.tenantId,
      tool: approval.tool,
      call_id: approval.callId ?? null,
      reason: approval.reason ?? '',
    }),
    signal: AbortSignal.timeout(Number(process.env.APPROVAL_TIMEOUT_MS ?? 30_000)),
  });
  if (!response.ok) return { approved: false, by: `http-${response.status}` };
  const body = await response.json().catch(() => ({}));
  return { approved: Boolean(body.approved), by: body.by ?? 'remote' };
}

function badRequest(message) {
  return Object.assign(new Error(message), { statusCode: 400 });
}

function conflict(message) {
  return Object.assign(new Error(message), { statusCode: 409 });
}
