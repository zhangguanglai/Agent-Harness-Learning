/**
 * Backend 工厂：mock（真实 spawn）/ loopback（进程内）/ sdk（真实 DSH）。
 *
 * 环境变量：
 *   DSH_BACKEND=mock|loopback|sdk   （默认 mock）
 *   DSH_BIN=dsh                     （sdk 模式）
 *   DSH_ARGS="--profile sdk"
 *   DSH_PROVIDER=deepseek  DSH_MODEL=deepseek-chat
 *   DEEPSEEK_API_KEY=...            （由 DSH 进程读取）
 */
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { LoopbackBackend } from './backends/loopback.js';
import { SdkBackend } from './backends/sdk.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const mockPath = path.resolve(here, '..', 'bin', 'mock-dsh.js');

export function createBackendFactory(env = process.env) {
  const mode = env.DSH_BACKEND ?? 'mock';
  const steps = Number(env.MOCK_STEPS ?? 3);
  const delay = Number(env.MOCK_DELAY_MS ?? 50);
  const needApproval = env.MOCK_APPROVAL === '1';

  if (mode === 'loopback') {
    const factory = (options) => new LoopbackBackend({ ...options, steps, delay, needApproval });
    factory.backendName = 'loopback-mock';
    return factory;
  }

  if (mode === 'mock') {
    const factory = (options) =>
      new SdkBackend({
        ...options,
        command: process.execPath,
        args: [mockPath],
        provider: 'mock',
        model: 'mock-1',
        env: {
          MOCK_STEPS: String(steps),
          MOCK_DELAY_MS: String(delay),
          MOCK_APPROVAL: needApproval ? '1' : '0',
        },
      });
    factory.backendName = 'mock-dsh';
    return factory;
  }

  if (mode !== 'sdk') throw new Error(`unknown DSH_BACKEND: ${mode}`);

  const command = env.DSH_BIN ?? 'dsh';
  const args = (env.DSH_ARGS ?? '--profile sdk').split(/\s+/).filter(Boolean);
  const factory = (options) =>
    new SdkBackend({
      ...options,
      command,
      args,
      provider: env.DSH_PROVIDER ?? 'deepseek',
      model: env.DSH_MODEL ?? 'deepseek-chat',
    });
  factory.backendName = 'dsh-sdk';
  return factory;
}
