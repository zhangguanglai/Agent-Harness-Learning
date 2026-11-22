"""极简 OTLP/HTTP 收集器：收 span、按 trace 查、健康检查。

生产用 OTel Collector + Jaeger/Tempo；S0 只需要一个「能看见调用链」的落点，
顺便把 OTLP JSON 的形状验证清楚。
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class SpanStore:
    def __init__(self) -> None:
        self.spans: list[dict] = []
        self.lock = threading.Lock()

    def add(self, payload: dict) -> int:
        added = 0
        for resource in payload.get("resourceSpans", []):
            service = ""
            for attr in (resource.get("resource") or {}).get("attributes", []):
                if attr.get("key") == "service.name":
                    service = (attr.get("value") or {}).get("stringValue", "")
            for scope in resource.get("scopeSpans", []):
                for span in scope.get("spans", []):
                    record = {
                        "service": service,
                        "trace_id": span.get("traceId", ""),
                        "span_id": span.get("spanId", ""),
                        "parent_span_id": span.get("parentSpanId", ""),
                        "name": span.get("name", ""),
                        "start_ns": int(span.get("startTimeUnixNano", "0")),
                        "end_ns": int(span.get("endTimeUnixNano", "0")),
                        "status": (span.get("status") or {}).get("code", 0),
                        "attributes": {
                            attr.get("key"): next(iter((attr.get("value") or {}).values()), None)
                            for attr in span.get("attributes", [])
                        },
                        "received_at": time.time(),
                    }
                    with self.lock:
                        self.spans.append(record)
                    added += 1
        return added

    def snapshot(self) -> list[dict]:
        with self.lock:
            return list(self.spans)

    def clear(self) -> None:
        with self.lock:
            self.spans.clear()


def make_server(store: SpanStore, host: str = "127.0.0.1", port: int = 4318) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            return

        def _json(self, code: int, payload: dict | list) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                return self._json(200, {"ok": True, "spans": len(store.snapshot())})
            if self.path.startswith("/spans"):
                return self._json(200, {"spans": store.snapshot()})
            return self._json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/v1/traces":
                return self._json(404, {"error": "not found"})
            length = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return self._json(400, {"error": "invalid json"})
            added = store.add(payload)
            return self._json(200, {"partialSuccess": {"rejectedSpans": 0}, "accepted": added})

        def do_DELETE(self) -> None:  # noqa: N802
            if self.path == "/spans":
                store.clear()
                return self._json(200, {"cleared": True})
            return self._json(404, {"error": "not found"})

    class QuietServer(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            exc = sys.exc_info()[1]
            if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
                return
            super().handle_error(request, client_address)

    return QuietServer((host, port), Handler)


def main() -> int:
    parser = argparse.ArgumentParser(description="OTLP/HTTP JSON collector（S0 用）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4318)
    parser.add_argument("--out", default=None, help="把 span 追加写入 JSONL")
    args = parser.parse_args()

    store = SpanStore()
    server = make_server(store, args.host, args.port)
    print(f"OTLP collector listening on http://{args.host}:{args.port}  (POST /v1/traces, GET /spans)")
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if args.out:
            Path(args.out).write_text(
                "\n".join(json.dumps(span, ensure_ascii=False) for span in store.snapshot()) + "\n",
                encoding="utf-8",
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
