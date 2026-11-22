"""`python -m orchestrator`：启动评估编排服务。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .service import Orchestrator, make_server


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    parser = argparse.ArgumentParser(prog="orchestrator", description="场景 A 评估编排服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--data", default=str(Path(__file__).parent / "_data"), help="数据目录（SQLite + 产物）")
    parser.add_argument("--approval-timeout", type=float, default=600.0)
    parser.add_argument("--worker-url", default=None, help="spike/dsh-worker 地址，如 http://127.0.0.1:8080")
    parser.add_argument(
        "--worker-approval",
        choices=["auto", "wait"],
        default="auto",
        help="worker 内部工具审批策略：auto=自动放行，wait=走人工审批闸门",
    )
    args = parser.parse_args(argv)

    orchestrator = Orchestrator(
        args.data,
        approval_timeout_s=args.approval_timeout,
        worker_url=args.worker_url,
        worker_approval=args.worker_approval,
    )
    server = make_server(orchestrator, args.host, args.port)
    print(f"评估编排服务已启动: http://{args.host}:{server.server_address[1]}")
    if args.worker_url:
        print(f"agent 阶段交给 worker: {args.worker_url}（审批策略 {args.worker_approval}）")
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\n停止中…")
    finally:
        server.server_close()
        orchestrator.store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
