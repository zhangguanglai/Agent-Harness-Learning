/**
 * S0 验收测试（离线，不需要模型 Key）。
 *
 * 默认走 loopback：进程内 PassThrough + 同一套 NDJSON JSON-RPC。
 * 真实 spawn 路径由 test/spawn.test.js 覆盖（沙箱内自动跳过）。
 *
 * 覆盖：健康检查、提交、SSE 事件、JSON 事件游标、取消、审批往返、幂等冲突。
 */
import { after, before, describe, it } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';

import { createWorker } from '../src/server.js';

let worker;
let base;
let approvalStub;
let approvalUrl;

before(async () => {
  const env = {
    ...process.env,
    DSH_BACKEND: 'loopback',
    MOCK_STEPS: '3',
    MOCK_DELAY_MS: '40',
  };
  worker = createWorker({ env });
  await new Promise((resolve) => worker.server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${worker.server.address().port}`;

  // 审批桩：任何审批都批准，并由 tester 留痕
  approvalStub = http.createServer((req, res) => {
    let body = '';
    req.on('data', (chunk) => (body += chunk));
    req.on('end', () => {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ approved: true, by: 'tester' }));
    });
  });
  await new Promise((resolve) => approvalStub.listen(0, '127.0.0.1', resolve));
  approvalUrl = `http://127.0.0.1:${approvalStub.address().port}/approve`;
});

after(async () => {
  await worker.manager.shutdown();
  await new Promise((resolve) => worker.server.close(resolve));
  await new Promise((resolve) => approvalStub.close(resolve));
});

async function post(path, body) {
  const response = await fetch(`${base}${path}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  return { status: response.status, body: await response.json() };
}

async function collectSSE(path, { timeoutMs = 10_000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const response = await fetch(`${base}${path}`, {
    headers: { accept: 'text/event-stream' },
    signal: controller.signal,
  });
  assert.equal(response.status, 200);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const events = [];
  let buffer = '';
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let index;
      while ((index = buffer.indexOf('\n\n')) >= 0) {
        const chunk = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        let data = null;
        let eventName = 'message';
        for (const line of chunk.split('\n')) {
          if (line.startsWith('data:')) data = line.slice(5).trim();
          else if (line.startsWith('event:')) eventName = line.slice(6).trim();
        }
        if (!data) continue;
        if (eventName === 'done') {
          await reader.cancel();
          return events;
        }
        events.push({ event: eventName, data: JSON.parse(data) });
      }
    }
  } finally {
    clearTimeout(timer);
  }
  return events;
}

describe('dsh-worker', () => {
  it('healthz 报告后端与运行数', async () => {
    const response = await fetch(`${base}/healthz`);
    const body = await response.json();
    assert.equal(response.status, 200);
    assert.equal(body.ok, true);
    assert.equal(body.backend, 'loopback-mock');
  });

  it('提交后通过 SSE 收到单调递增的事件流并以 done 收尾', async () => {
    const submitted = await post('/v1/runs', { run_id: 'run-sse-1', tenant_id: 't1', task: '写一份纪要' });
    assert.equal(submitted.status, 202);

    const events = await collectSSE('/v1/runs/run-sse-1/events');
    const seqs = events.map((e) => e.data.seq);
    assert.deepEqual([...seqs].sort((a, b) => a - b), seqs, 'seq 必须单调');
    const types = events.map((e) => e.data.type);
    assert.ok(types.includes('run.queued'));
    assert.ok(types.includes('run.started'));
    assert.ok(types.includes('session.event'), '应转发 DSH 的 session.event');
    assert.equal(types.at(-1), 'run.succeeded');
    assert.ok(events.at(-1).data.payload.final_response.includes('done'));
  });

  it('支持 JSON 事件与 after 游标', async () => {
    const all = await (await fetch(`${base}/v1/runs/run-sse-1/events`)).json();
    assert.ok(all.events.length > 3);
    const after = all.events[1].seq;
    const tail = await (await fetch(`${base}/v1/runs/run-sse-1/events?after=${after}`)).json();
    assert.ok(tail.events.every((e) => e.seq > after));
    assert.equal(tail.events.at(-1).type, 'run.succeeded');
  });

  it('取消会终止运行并写入 run.cancelled', async () => {
    const slowEnv = { ...process.env, DSH_BACKEND: 'loopback', MOCK_STEPS: '80', MOCK_DELAY_MS: '100' };
    const slowWorker = createWorker({ env: slowEnv });
    await new Promise((resolve) => slowWorker.server.listen(0, '127.0.0.1', resolve));
    const slowBase = `http://127.0.0.1:${slowWorker.server.address().port}`;
    try {
      const submitted = await fetch(`${slowBase}/v1/runs`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ run_id: 'run-cancel', task: '长任务' }),
      });
      assert.equal(submitted.status, 202);
      await new Promise((resolve) => setTimeout(resolve, 250));

      const cancel = await fetch(`${slowBase}/v1/runs/run-cancel/cancel`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ reason: 'test' }),
      });
      assert.equal(cancel.status, 202);

      const deadline = Date.now() + 5_000;
      let status;
      while (Date.now() < deadline) {
        status = await (await fetch(`${slowBase}/v1/runs/run-cancel`)).json();
        if (status.status === 'cancelled') break;
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
      assert.equal(status.status, 'cancelled');

      const events = await (await fetch(`${slowBase}/v1/runs/run-cancel/events`)).json();
      assert.ok(events.events.some((e) => e.type === 'run.cancelling'));
      assert.equal(events.events.at(-1).type, 'run.cancelled');
    } finally {
      await slowWorker.manager.shutdown();
      await new Promise((resolve) => slowWorker.server.close(resolve));
    }
  });

  it('审批往返：DSH 的 approval/request 打到控制面并回填', async () => {
    const approvalEnv = {
      ...process.env,
      DSH_BACKEND: 'loopback',
      MOCK_STEPS: '3',
      MOCK_DELAY_MS: '30',
      MOCK_APPROVAL: '1',
    };
    const approvalWorker = createWorker({ env: approvalEnv });
    await new Promise((resolve) => approvalWorker.server.listen(0, '127.0.0.1', resolve));
    const approvalBase = `http://127.0.0.1:${approvalWorker.server.address().port}`;
    try {
      await fetch(`${approvalBase}/v1/runs`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ run_id: 'run-approval', task: '写入交付物', approval_url: approvalUrl }),
      });
      const deadline = Date.now() + 5_000;
      let events;
      while (Date.now() < deadline) {
        events = await (await fetch(`${approvalBase}/v1/runs/run-approval/events`)).json();
        if (events.events.some((e) => e.type === 'run.succeeded')) break;
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
      const types = events.events.map((e) => e.type);
      assert.ok(types.includes('approval.requested'), types.join(','));
      assert.ok(types.includes('approval.resolved'));
      const resolved = events.events.find((e) => e.type === 'approval.resolved');
      assert.equal(resolved.payload.approved, true);
      assert.equal(resolved.payload.by, 'tester');
      assert.equal(types.at(-1), 'run.succeeded');
    } finally {
      await approvalWorker.manager.shutdown();
      await new Promise((resolve) => approvalWorker.server.close(resolve));
    }
  });

  it('同一 run_id 重复提交返回 409', async () => {
    const again = await post('/v1/runs', { run_id: 'run-sse-1', task: '重复' });
    assert.equal(again.status, 409);
  });
});
