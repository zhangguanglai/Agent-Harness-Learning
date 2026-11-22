/**
 * spawn 路径测试：真实启动 `node bin/mock-dsh.js` 子进程。
 *
 * 受限沙箱里 Node 无法用管道 spawn（EPERM），此时跳过；
 * 在普通终端/CI 里这条测试会真正跑通「子进程 + stdio + 事件转发 + 取消」。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { createWorker } from '../src/server.js';

test('spawn 子进程：提交 → 事件 → 终态', async (t) => {
  const worker = createWorker({
    env: { ...process.env, DSH_BACKEND: 'mock', MOCK_STEPS: '2', MOCK_DELAY_MS: '20' },
  });
  await new Promise((resolve) => worker.server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${worker.server.address().port}`;

  try {
    const response = await fetch(`${base}/v1/runs`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ run_id: 'spawn-1', task: '看看工作区' }),
    });
    assert.equal(response.status, 202);

    const deadline = Date.now() + 10_000;
    let status;
    let events;
    while (Date.now() < deadline) {
      status = await (await fetch(`${base}/v1/runs/spawn-1`)).json();
      if (['succeeded', 'failed', 'cancelled'].includes(status.status)) break;
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    events = (await (await fetch(`${base}/v1/runs/spawn-1/events`)).json()).events;

    const failure = events.find((event) => event.type === 'run.failed');
    if (failure && /EPERM|pipe/i.test(failure.payload?.error ?? '')) {
      t.skip('沙箱禁止管道 spawn，跳过 spawn 路径（在普通终端或 CI 运行）');
      return;
    }
    assert.equal(status.status, 'succeeded', JSON.stringify(status));
    assert.equal(events.at(-1).type, 'run.succeeded');
  } finally {
    await worker.manager.shutdown();
    await new Promise((resolve) => worker.server.close(resolve));
  }
});
