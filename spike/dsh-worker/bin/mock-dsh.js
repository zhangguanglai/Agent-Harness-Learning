#!/usr/bin/env node
/**
 * Mock DSH SDK runtime（子进程形态）。
 *
 * 在受限沙箱里 Node 无法用管道 spawn 子进程（EPERM），此时测试会走 loopback；
 * 在普通终端或 CI 里，这个文件验证的是真实 spawn + stdio 路径。
 */
import { createMockRuntime } from '../src/testing/mock-runtime.js';

createMockRuntime({
  input: process.stdin,
  output: process.stdout,
  steps: Number(process.env.MOCK_STEPS ?? 3),
  delay: Number(process.env.MOCK_DELAY_MS ?? 50),
  needApproval: process.env.MOCK_APPROVAL === '1',
  onShutdown: () => process.exit(0),
});

process.stdin.on('close', () => process.exit(0));
process.on('SIGTERM', () => process.exit(0));
process.on('SIGINT', () => process.exit(0));
