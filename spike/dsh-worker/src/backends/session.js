/**
 * 会话后端基类：把「握手 + 提交 prompt + 审批应答」这段协议逻辑与传输解耦。
 *
 * 子类只需实现 `_connect()`（返回 {input, output}）与 `cancel()/stop()`：
 * - `SdkBackend`：spawn 真实 DSH 进程；
 * - `LoopbackBackend`：进程内 PassThrough，测试用（沙箱里 spawn 带管道会 EPERM）。
 */
import { JsonRpcPeer } from '../jsonrpc.js';

export class SessionBackend {
  constructor({ provider = 'deepseek', model = 'deepseek-chat', cwd, onEvent, onApproval, onExit }) {
    this.provider = provider;
    this.model = model;
    this.cwd = cwd;
    this.onEvent = onEvent ?? (() => {});
    this.onApproval = onApproval;
    this.onExit = onExit;
    this.peer = null;
    this.closed = false;
  }

  async start({ task, sessionId }) {
    const { input, output } = await this._connect();
    this.peer = new JsonRpcPeer({
      input,
      output,
      name: 'dsh-sdk',
      onNotification: (method, params) => this.onEvent(method, params),
      onRequest: async (method, params) => {
        if (method === 'approval/request') {
          const outcome = await this.onApproval?.({
            tool: params.toolName,
            callId: params.callId,
            reason: params.reason,
            sessionId: params.sessionId,
          });
          return { outcome: outcome ?? 'rejected' };
        }
        if (method === 'user-question/ask') {
          // S0 不做交互式提问：直接取消，避免把进程挂死
          return { cancelled: true };
        }
        throw Object.assign(new Error(`unhandled server request: ${method}`), { code: -32601 });
      },
    });
    this.peer.start();

    await this.peer.request(
      'initialize',
      {
        provider: this.provider,
        model: this.model,
        cwd: this.cwd,
        approvalRequests: true,
        capabilities: { userQuestions: false },
      },
      Number(process.env.DSH_INIT_TIMEOUT_MS ?? 60_000),
    );

    return this.peer.request(
      'session/query',
      { sessionId, contentBlocks: [{ type: 'text', text: task }] },
      Number(process.env.DSH_QUERY_TIMEOUT_MS ?? 30_000),
    );
  }

  async cancel() {}

  async stop() {
    this.closed = true;
    this.peer?.close();
  }
}
