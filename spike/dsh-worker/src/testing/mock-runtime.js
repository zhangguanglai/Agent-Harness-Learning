/**
 * Mock DSH runtime 的核心（进程内可复用）。
 *
 * 同时被两处使用：
 * - `bin/mock-dsh.js`：作为子进程运行，测真实的 spawn + stdio 路径；
 * - `src/backends/loopback.js`：进程内 PassThrough，供沙箱内测试
 *   （受限沙箱会禁止 Node 用管道 spawn 子进程，报 EPERM）。
 */
import { JsonRpcPeer } from '../jsonrpc.js';

export function createMockRuntime({
  input,
  output,
  steps = 3,
  delay = 50,
  needApproval = false,
  name = 'mock-dsh',
  onShutdown,
}) {
  let sessionId = 'mock-session';
  let stopped = false;
  const timers = new Set();

  const peer = new JsonRpcPeer({ input, output, name, onRequest: handleRequest });
  peer.start();

  function sleep(ms) {
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        timers.delete(timer);
        resolve();
      }, ms);
      timers.add(timer);
    });
  }

  function emit(event) {
    if (!stopped) peer.notify('session.event', { sessionId, event });
  }

  async function handleRequest(method, params = {}) {
    switch (method) {
      case 'initialize':
        return { serverInfo: { name: 'deepseek-harness-sdk-runtime', version: '0.2.2-rc.5-mock' } };
      case 'session/query':
      case 'session/prompt':
        runTurn(params).catch(() => {});
        return method === 'session/query'
          ? { kind: 'prompt', messageId: `msg_${Date.now()}` }
          : { messageId: `msg_${Date.now()}` };
      case 'session/wait':
      case 'session/resume':
        return {};
      case 'shutdown':
        stop();
        setTimeout(() => onShutdown?.(), 5);
        return {};
      default:
        throw Object.assign(new Error(`mock does not implement ${method}`), { code: -32601 });
    }
  }

  async function runTurn(params = {}) {
    sessionId = params.sessionId ?? sessionId;
    emit({ type: 'user/message', data: { role: 'user', content: [{ type: 'text', text: params.contentBlocks?.[0]?.text ?? '(mock input)' }] } });
    peer.notify('session.status', { sessionId, status: 'running' });

    for (let step = 1; step <= steps; step += 1) {
      await sleep(delay);
      if (stopped) return;
      if (step === 1) {
        emit({ type: 'tool/call', data: { name: 'kb.search', callId: 'mock-call-1', arguments: { query: '数据出境' } } });
      }
      emit({
        type: 'assistant/message',
        data: {
          step,
          message: { content: [{ type: 'text', text: `mock step ${step}` }] },
          usage: { input_tokens: 120 * step, output_tokens: 30 * step },
        },
      });
      if (step === 2) {
        emit({ type: 'tool/result', data: { callId: 'mock-call-1', name: 'kb.search', ok: true } });
      }
      if (needApproval && step === 2) {
        const outcome = await peer
          .request('approval/request', {
            sessionId,
            toolName: 'artifact.write',
            callId: `call_${step}`,
            reason: 'mock: writing a deliverable needs approval',
          })
          .catch((err) => ({ outcome: 'rejected', reason: err.message }));
        if (stopped) return;
        emit({ type: 'approval/result', data: { outcome: outcome.outcome } });
      }
    }

    emit({ type: 'assistant/message', step: steps, text: 'mock done' });
    peer.notify('session.query.completed', {
      sessionId,
      commandId: 'cmd_mock',
      finalResponse: 'mock done',
      finishReason: 'stop',
    });
    peer.notify('session.status', { sessionId, status: 'idle' });
  }

  function stop() {
    stopped = true;
    for (const timer of timers) clearTimeout(timer);
    timers.clear();
    peer.close();
  }

  return { peer, stop };
}
