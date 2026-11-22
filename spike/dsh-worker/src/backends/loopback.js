/**
 * 进程内后端：用 PassThrough 代替 stdio，跑同一套 NDJSON JSON-RPC。
 *
 * 为什么需要它：受限沙箱禁止 Node 以管道 spawn 子进程（EPERM），
 * 于是测试用 loopback 验证协议与状态机，另有一条 spawn 测试在允许的环境执行。
 */
import { PassThrough } from 'node:stream';

import { createMockRuntime } from '../testing/mock-runtime.js';
import { SessionBackend } from './session.js';

export class LoopbackBackend extends SessionBackend {
  constructor({ steps = 3, delay = 40, needApproval = false, ...rest }) {
    super(rest);
    this.mockOptions = { steps, delay, needApproval };
    this.mock = null;
  }

  async _connect() {
    const toServer = new PassThrough();
    const toClient = new PassThrough();
    this.mock = createMockRuntime({ input: toServer, output: toClient, ...this.mockOptions });
    return { input: toClient, output: toServer };
  }

  async cancel() {
    this.mock?.stop();
    this.closed = true;
  }

  async stop() {
    this.mock?.stop();
    await super.stop();
  }
}
