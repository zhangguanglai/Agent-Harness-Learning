"""`python -m api`：启动一个可用的 Harness HTTP 服务。"""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

from .auth import TokenRegistry
from .server import ApiConfig, HarnessAPI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="api", description="mini-harness HTTP API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--workspace-root", default=str(Path.cwd() / ".harness" / "workspaces"))
    parser.add_argument("--db", default=None, help="SQLite 事件库路径；不传则用内存")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument(
        "--token",
        action="append",
        default=[],
        metavar="TOKEN=TENANT:USER:ROLES",
        help="可重复；默认 dev-token=demo:alice:operator",
    )
    args = parser.parse_args(argv)

    specs = args.token or ["dev-token=demo:alice:operator"]
    tokens = TokenRegistry.from_specs(specs)
    config = ApiConfig(
        host=args.host,
        port=args.port,
        workspace_root=Path(args.workspace_root).resolve(),
        db_path=Path(args.db).resolve() if args.db else None,
        workers=args.workers,
    )
    api = HarnessAPI(config, tokens)
    httpd = api.build_httpd()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    print(f"Harness API 已启动: http://{config.host}:{api.port}  (tokens: {', '.join(specs)})")
    print(f"工作区根目录: {config.workspace_root}")
    print("Ctrl+C 停止。")
    try:
        httpd.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\n停止中…")
    finally:
        api.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
