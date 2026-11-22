#!/usr/bin/env node
/**
 * Probe：连真实的 `dsh --profile sdk`，把收到的每一帧原样打到 stdout 并存成 JSONL。
 *
 * 用途：S0-3 的「录制真实帧」步骤 —— 拿到真实 session.event 的字段形状后，
 * 回填 src/protocol.js 的 normalizeNotification，并把它固化进测试夹具。
 *
 * 前置：
 *   - Node ≥ 24.9（DSH 的 engines 要求；本机默认 Node 20 不满足）
 *   - `npm i -g @deepseek-ai/dsh` 或设置 DSH_BIN
 *   - DEEPSEEK_API_KEY 已配置
 *
 * 用法：
 *   node probe.js --out probe-frames.jsonl --task "列出当前目录的文件"
 */
import fs from 'node:fs/promises';
import { spawn } from 'node:child_process';

import { JsonRpcPeer } from './src/jsonrpc.js';

const args = process.argv.slice(2);
function flag(name, fallback) {
  const index = args.indexOf(`--${name}`);
  return index >= 0 ? args[index + 1] : fallback;
}

const bin = process.env.DSH_BIN ?? 'dsh';
const argv = (process.env.DSH_ARGS ?? '--profile sdk').split(/\s+/).filter(Boolean);
const out = flag('out', 'probe-frames.jsonl');
const task = flag('task', '列出当前目录的文件，然后说明你做了什么。');
const timeoutMs = Number(flag('timeout', '120000'));

const frames = [];
const child = spawn(bin, argv, { stdio: ['pipe', 'pipe', 'pipe'], env: process.env, windowsHide: true });
child.stderr.setEncoding('utf8');
child.stderr.on('data', (chunk) => process.stderr.write(`[dsh stderr] ${chunk}`));

const peer = new JsonRpcPeer({
  input: child.stdout,
  output: child.stdin,
  name: 'probe',
  onNotification: (method, params) => {
    record({ direction: 'notification', method, params });
  },
  onRequest: async (method, params) => {
    record({ direction: 'request', method, params });
    // 探测阶段：审批一律拒绝，避免替代真人做决定
    if (method === 'approval/request') return { outcome: 'rejected' };
    if (method === 'user-question/ask') return { cancelled: true };
    throw Object.assign(new Error(`unhandled: ${method}`), { code: -32601 });
  },
});
peer.start();

function record(frame) {
  const entry = { ts: new Date().toISOString(), ...frame };
  frames.push(entry);
  process.stdout.write(`${JSON.stringify(entry)}\n`);
}

const timer = setTimeout(async () => {
  process.stderr.write('\nprobe timeout, shutting down\n');
  await finish(2);
}, timeoutMs);

async function finish(code) {
  clearTimeout(timer);
  try {
    await peer.request('shutdown', {}, 3_000);
  } catch {
    child.kill('SIGKILL');
  }
  await fs.writeFile(out, frames.map((f) => JSON.stringify(f)).join('\n') + '\n', 'utf8');
  process.stderr.write(`\nwrote ${frames.length} frames to ${out}\n`);
  process.exit(code);
}

try {
  const init = await peer.request('initialize', {
    provider: process.env.DSH_PROVIDER ?? 'deepseek',
    model: process.env.DSH_MODEL ?? 'deepseek-chat',
    cwd: process.cwd(),
    approvalRequests: true,
    capabilities: { userQuestions: false },
  });
  record({ direction: 'response', method: 'initialize', result: init });

  await peer.request('session/query', {
    sessionId: `probe-${Date.now()}`,
    contentBlocks: [{ type: 'text', text: task }],
  });
  process.stderr.write('probe: prompt queued, collecting frames…\n');

  // 等 session.status idle 或 query.completed
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 100));
    if (frames.some((f) => f.method === 'session.query.completed')) break;
  }
  await finish(0);
} catch (err) {
  process.stderr.write(`probe failed: ${err.stack ?? err}\n`);
  await finish(1);
}
