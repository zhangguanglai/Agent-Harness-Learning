/**
 * NDJSON JSON-RPC 2.0 peer.
 *
 * 帧规则（来自 @deepseek-ai/dsh-sdk-protocol）：
 *   - 一行一帧，`\n` 结尾；
 *   - `id + method` = 请求；`id` = 响应；`method` = 通知；
 *   - 坏行忽略；未注册方法回 -32601；处理器异常回 -32603。
 *
 * 这个类同时用于两端：worker 侧（驱动 DSH）与 mock DSH 侧（测试替身）。
 */
import { randomUUID } from 'node:crypto';

export class JsonRpcPeer {
  constructor({ input, output, onRequest, onNotification = () => {}, name = 'peer' }) {
    this.input = input;
    this.output = output;
    this.onRequest = onRequest || (async () => {
      throw Object.assign(new Error('no request handler'), { code: -32601 });
    });
    this.onNotification = onNotification;
    this.name = name;
    this._pending = new Map();
    this._buffer = '';
    this._closed = false;
  }

  start() {
    this.input.setEncoding?.('utf8');
    this.input.on('data', (chunk) => this._onData(chunk));
    this.input.on('close', () => this._failAll(new Error(`${this.name}: transport closed`)));
    this.input.on('error', (err) => this._failAll(err));
  }

  _onData(chunk) {
    this._buffer += chunk;
    let index;
    while ((index = this._buffer.indexOf('\n')) >= 0) {
      const line = this._buffer.slice(0, index).trim();
      this._buffer = this._buffer.slice(index + 1);
      if (!line) continue;
      let frame;
      try {
        frame = JSON.parse(line);
      } catch {
        continue; // 坏行忽略，与协议一致
      }
      this._dispatch(frame);
    }
  }

  _dispatch(frame) {
    if (frame.id !== undefined && frame.method) return this._handleRequest(frame);
    if (frame.id !== undefined) return this._handleResponse(frame);
    if (frame.method) this.onNotification(frame.method, frame.params ?? {});
  }

  _handleResponse(frame) {
    const pending = this._pending.get(frame.id);
    if (!pending) return;
    this._pending.delete(frame.id);
    clearTimeout(pending.timer);
    if (frame.error) {
      const error = new Error(frame.error.message || 'rpc error');
      error.code = frame.error.code;
      error.data = frame.error.data;
      pending.reject(error);
    } else {
      pending.resolve(frame.result ?? {});
    }
  }

  _handleRequest(frame) {
    Promise.resolve()
      .then(() => this.onRequest(frame.method, frame.params ?? {}))
      .then((result) => this._write({ jsonrpc: '2.0', id: frame.id, result }))
      .catch((err) =>
        this._write({
          jsonrpc: '2.0',
          id: frame.id,
          error: { code: err?.code ?? -32603, message: err?.message ?? String(err) },
        }),
      );
  }

  request(method, params = {}, timeoutMs = 30_000) {
    const id = randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this._pending.delete(id);
        reject(new Error(`${this.name}: rpc timeout ${method}`));
      }, timeoutMs);
      this._pending.set(id, { resolve, reject, timer });
      this._write({ jsonrpc: '2.0', id, method, params });
    });
  }

  notify(method, params = {}) {
    this._write({ jsonrpc: '2.0', method, params });
  }

  _write(frame) {
    if (this._closed) return;
    try {
      this.output.write(`${JSON.stringify(frame)}\n`);
    } catch (err) {
      this._failAll(err);
    }
  }

  _failAll(error) {
    for (const [id, pending] of this._pending) {
      clearTimeout(pending.timer);
      pending.reject(error);
      this._pending.delete(id);
    }
  }

  close() {
    this._closed = true;
    this._failAll(new Error(`${this.name}: closed`));
  }
}
