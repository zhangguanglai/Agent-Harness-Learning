/**
 * 真实后端：spawn `dsh --profile sdk`，stdio 上跑 NDJSON JSON-RPC。
 *
 * 关键事实：SDK 协议**没有取消方法**，官方说明「客户端通过关闭 runtime 进程放弃一轮」。
 * 所以这里的 cancel 是杀进程（POSIX 杀进程组，Windows 用 taskkill /T，失败再兜底 kill）。
 */
import { spawn } from 'node:child_process';

import { SessionBackend } from './session.js';

export class SdkBackend extends SessionBackend {
  constructor({ command, args = [], env = {}, onStderr, ...rest }) {
    super(rest);
    this.command = command;
    this.args = args;
    this.env = env;
    this.onStderr = onStderr;
    this.child = null;
    this.exited = false;
  }

  async _connect() {
    this.child = spawn(this.command, this.args, {
      cwd: this.cwd,
      env: { ...process.env, ...this.env },
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
      detached: process.platform !== 'win32',
    });
    this.child.stderr?.setEncoding('utf8');
    this.child.stderr?.on('data', (chunk) => this.onStderr?.(String(chunk)));
    this.child.on('exit', (code, signal) => {
      this.exited = true;
      this.peer?.close();
      this.onExit?.(code, signal);
    });
    return { input: this.child.stdout, output: this.child.stdin };
  }

  async cancel() {
    const child = this.child;
    if (!child || this.exited) return;
    if (process.platform === 'win32') {
      await new Promise((resolve) => {
        const killer = spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
        const fallback = () => {
          try {
            child.kill('SIGKILL');
          } catch {
            /* already gone */
          }
          resolve();
        };
        killer.on('exit', fallback);
        killer.on('error', fallback);
      });
      return;
    }
    try {
      process.kill(-child.pid, 'SIGKILL');
    } catch {
      try {
        child.kill('SIGKILL');
      } catch {
        /* ignore */
      }
    }
  }

  async stop() {
    if (!this.child || this.exited) return super.stop();
    try {
      await this.peer?.request('shutdown', {}, 2_000);
    } catch {
      /* shutdown 失败就强杀 */
    }
    const deadline = Date.now() + 1_000;
    while (!this.exited && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    if (!this.exited) await this.cancel();
    return super.stop();
  }
}
