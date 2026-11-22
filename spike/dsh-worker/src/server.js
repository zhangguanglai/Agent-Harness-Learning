/**
 * HTTP worker：控制面 → 这个进程 → DSH SDK runtime（子进程）。
 *
 * 只做四件事：接单、转发事件（JSON/SSE）、取消、健康检查。
 * 业务语义（提示词、工具、审批策略）都在 DSH 侧的领域 bundle 里。
 */
import http from 'node:http';
import { pathToFileURL } from 'node:url';

import { RunManager } from './runner.js';
import { createBackendFactory } from './factory.js';
import { TERMINAL_TYPES } from './protocol.js';

const MAX_BODY_BYTES = 1024 * 1024;

export function createWorker({ env = process.env, backendFactory } = {}) {
  const factory = backendFactory ?? createBackendFactory(env);
  const manager = new RunManager({
    backendFactory: factory,
    maxWallMs: Number(env.MAX_WALL_MS ?? 15 * 60 * 1000),
  });

  const server = http.createServer((req, res) => {
    handle(req, res, manager, factory).catch((err) => {
      respondJson(res, err?.statusCode ?? 500, { error: err?.message ?? 'internal error' });
    });
  });

  return { server, manager, backend: factory.backendName ?? 'unknown' };
}

async function handle(req, res, manager, factory) {
  const url = new URL(req.url, 'http://worker.local');
  const path = url.pathname;

  if (req.method === 'GET' && path === '/healthz') {
    return respondJson(res, 200, {
      ok: true,
      backend: factory.backendName ?? 'unknown',
      runs: manager.runs.size,
      uptime_s: Math.round((Date.now() - manager.startedAt) / 1000),
    });
  }

  if (req.method === 'POST' && path === '/v1/runs') {
    const body = await readJson(req);
    const run = manager.create({
      runId: body.run_id,
      tenantId: body.tenant_id,
      task: body.task,
      workspace: body.workspace,
      preset: body.preset,
      limits: body.limits,
      approvalUrl: body.approval_url,
    });
    return respondJson(res, 202, {
      run_id: run.id,
      status: run.status,
      events_url: `/v1/runs/${encodeURIComponent(run.id)}/events`,
    });
  }

  const match = path.match(/^\/v1\/runs\/([^/]+)(?:\/(events|cancel))?$/);
  if (match) {
    const runId = decodeURIComponent(match[1]);
    const action = match[2];
    const run = manager.get(runId);
    if (!run) return respondJson(res, 404, { error: `run not found: ${runId}` });

    if (req.method === 'GET' && action === 'events') {
      const after = Number(url.searchParams.get('after') ?? 0) || 0;
      const accept = req.headers.accept ?? '';
      if (accept.includes('text/event-stream')) return streamEvents(req, res, manager, runId, after);
      return respondJson(res, 200, { events: manager.events(runId, after) });
    }

    if (req.method === 'POST' && action === 'cancel') {
      const body = await readJson(req).catch(() => ({}));
      const cancelled = manager.cancel(runId, body.reason ?? 'api_cancel');
      return respondJson(res, 202, { cancelled: Boolean(cancelled), status: manager.get(runId)?.status });
    }

    if (req.method === 'GET' && !action) {
      return respondJson(res, 200, {
        run_id: run.id,
        tenant_id: run.tenantId,
        status: run.status,
        last_seq: run.seq,
        events: run.events.length,
      });
    }
  }

  return respondJson(res, 404, { error: `unknown route: ${req.method} ${path}` });
}

function streamEvents(req, res, manager, runId, after) {
  res.writeHead(200, {
    'content-type': 'text/event-stream; charset=utf-8',
    'cache-control': 'no-cache',
    connection: 'close',
    'x-accel-buffering': 'no',
  });
  res.write('retry: 1000\n\n');

  let done = false;
  const send = (event) => {
    if (done) return;
    res.write(`id: ${event.seq}\ndata: ${JSON.stringify(event)}\n\n`);
    if (TERMINAL_TYPES.has(event.type)) {
      res.write(`event: done\ndata: ${JSON.stringify({ status: event.type.replace('run.', ''), seq: event.seq })}\n\n`);
      finish();
    }
  };

  const unsubscribe = manager.subscribe(runId, after, send);
  if (unsubscribe === null) return respondJson(res, 404, { error: `run not found: ${runId}` });

  const heartbeat = setInterval(() => {
    if (!done) res.write(': ping\n\n');
  }, 10_000);

  function finish() {
    if (done) return;
    done = true;
    clearInterval(heartbeat);
    unsubscribe?.();
    res.end();
  }

  req.on('close', () => {
    clearInterval(heartbeat);
    unsubscribe?.();
    done = true;
  });
}

function respondJson(res, status, payload) {
  if (res.headersSent) return res.end();
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(body),
  });
  res.end(body);
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(Object.assign(new Error('body too large'), { statusCode: 413 }));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => {
      if (chunks.length === 0) return resolve({});
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));
      } catch {
        reject(Object.assign(new Error('invalid json body'), { statusCode: 400 }));
      }
    });
    req.on('error', reject);
  });
}

async function main() {
  const env = process.env;
  const { server, manager, backend } = createWorker({ env });
  const port = Number(env.PORT ?? 8080);
  const host = env.HOST ?? '127.0.0.1';
  server.listen(port, host, () => {
    process.stdout.write(`dsh-worker listening on http://${host}:${port} (backend=${backend})\n`);
  });

  const stop = async () => {
    process.stdout.write('\ndsh-worker shutting down…\n');
    server.close();
    await manager.shutdown();
    process.exit(0);
  };
  process.on('SIGINT', stop);
  process.on('SIGTERM', stop);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((err) => {
    process.stderr.write(`dsh-worker fatal: ${err.stack ?? err}\n`);
    process.exit(1);
  });
}
