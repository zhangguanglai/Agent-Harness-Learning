"""`python -m orchestrator`：启动评估编排服务。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from api.auth import TokenRegistry

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
        "--token",
        action="append",
        default=[],
        metavar="TOKEN=TENANT:USER:ROLES",
        help="启用鉴权；可重复。例：--token t-acme=acme:alice:operator",
    )
    parser.add_argument("--artifact-dir", default=None, help="产物目录（默认 <data>/artifacts）")
    parser.add_argument("--jwt-secret", default=None, help="启用 JWT(HS256) 鉴权；claims 需含 tenant/sub/roles")
    parser.add_argument("--jwt-issuer", default=None)
    parser.add_argument("--jwt-audience", default=None)
    parser.add_argument("--dual-approval", action="store_true", help="安全评估路径要求两名审批人")
    parser.add_argument("--s3-endpoint", default=None, help="S3 兼容端点，如 http://127.0.0.1:9000")
    parser.add_argument("--s3-bucket", default=None, help="产物 bucket；设置后启用 S3 存储")
    parser.add_argument("--s3-region", default="us-east-1")
    parser.add_argument("--s3-access-key", default=None)
    parser.add_argument("--s3-secret-key", default=None)
    parser.add_argument("--s3-prefix", default="")
    parser.add_argument("--approval-sla", type=float, default=4 * 3600, help="审批 SLA（秒），超时升级")
    parser.add_argument("--quota-concurrent", type=int, default=3)
    parser.add_argument("--quota-daily-runs", type=int, default=200)
    parser.add_argument("--quota-daily-tokens", type=int, default=2_000_000)
    parser.add_argument(
        "--worker-approval",
        choices=["auto", "wait"],
        default="auto",
        help="worker 内部工具审批策略：auto=自动放行，wait=走人工审批闸门",
    )
    args = parser.parse_args(argv)

    tokens = TokenRegistry.from_specs(args.token) if args.token else None
    authenticator = None
    if args.jwt_secret:
        from .auth import JwtAuthenticator

        authenticator = JwtAuthenticator(args.jwt_secret, issuer=args.jwt_issuer, audience=args.jwt_audience)
    from .quota import QuotaPolicy

    orchestrator = Orchestrator(
        args.data,
        approval_timeout_s=args.approval_timeout,
        worker_url=args.worker_url,
        worker_approval=args.worker_approval,
        tokens=tokens,
        authenticator=authenticator,
        quota_policy=QuotaPolicy(
            max_concurrent=args.quota_concurrent,
            daily_runs=args.quota_daily_runs,
            daily_tokens=args.quota_daily_tokens,
        ),
        approval_sla_seconds=args.approval_sla,
        second_approval_paths={"security_assessment"} if args.dual_approval else set(),
        artifact_dir=Path(args.artifact_dir) if args.artifact_dir else None,
    )
    server = make_server(orchestrator, args.host, args.port)
    print(f"评估编排服务已启动: http://{args.host}:{server.server_address[1]}")
    if args.worker_url:
        print(f"agent 阶段交给 worker: {args.worker_url}（审批策略 {args.worker_approval}）")
    if authenticator:
        print("鉴权：JWT(HS256)")
    else:
        print(f"鉴权：{'静态 token（' + str(len(args.token)) + ' 个）' if tokens else '未启用（开发模式）'}")
    print(f"配额：并发 {args.quota_concurrent} / 日运行 {args.quota_daily_runs} / 日 token {args.quota_daily_tokens}")
    print(f"双人审批：{'开启' if args.dual_approval else '关闭'}，SLA {args.approval_sla:.0f}s")
    print(f"产物存储：{'s3://' + args.s3_bucket if args.s3_bucket else '本地目录'}")
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
