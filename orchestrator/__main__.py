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
    args = parser.parse_args(argv)

    orchestrator = Orchestrator(args.data, approval_timeout_s=args.approval_timeout)
    server = make_server(orchestrator, args.host, args.port)
    print(f"评估编排服务已启动: http://{args.host}:{server.server_address[1]}")
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
