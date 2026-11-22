"""OTLP/HTTP JSON 追踪（标准库实现）。

为什么自己写而不是引 OpenTelemetry SDK：
- 仓库零依赖，S0 只需把「一次评估的调用链」串起来；
- OTLP/HTTP JSON 是公开协议，手写 exporter 反而更容易看清 span 结构；
- 生产直接换官方 SDK，调用点（`tracer.start/span.end`）不变。

关键点：`traceparent` 遵循 W3C 格式（`00-<trace_id>-<span_id>-01`），
编排层把它随提交请求传给 worker，worker 再作为自己的父 span。
"""

from __future__ import annotations

import json
import os
import secrets
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

SPAN_KIND_INTERNAL = 1
SPAN_KIND_SERVER = 2
SPAN_KIND_CLIENT = 3
STATUS_OK = 1
STATUS_ERROR = 2


def _hex(nbytes: int) -> str:
    return secrets.token_hex(nbytes)


def parse_traceparent(value: str | None) -> tuple[str, str] | None:
    if not value:
        return None
    parts = value.split("-")
    if len(parts) != 4 or parts[0] != "00":
        return None
    trace_id, span_id = parts[1], parts[2]
    if len(trace_id) != 32 or len(span_id) != 16:
        return None
    return trace_id, span_id


@dataclass
class Span:
    tracer: "Tracer"
    name: str
    trace_id: str
    span_id: str
    parent_span_id: str = ""
    kind: int = SPAN_KIND_INTERNAL
    attributes: dict = field(default_factory=dict)
    start_ns: int = field(default_factory=lambda: time.time_ns())
    end_ns: int = 0
    status: int = 0

    @property
    def traceparent(self) -> str:
        return f"00-{self.trace_id}-{self.span_id}-01"

    def set(self, key: str, value) -> "Span":
        self.attributes[key] = value
        return self

    def end(self, error: str | None = None) -> "Span":
        self.end_ns = time.time_ns()
        self.status = STATUS_ERROR if error else STATUS_OK
        if error:
            self.attributes["error"] = error[:300]
        self.tracer.export(self)
        return self


class Tracer:
    def __init__(self, endpoint: str | None = None, service: str = "orchestrator", attributes: dict | None = None):
        self.endpoint = (endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "").rstrip("/")
        self.service = service
        self.attributes = attributes or {}
        self.exported = 0
        self.failed = 0

    @property
    def enabled(self) -> bool:
        return bool(self.endpoint)

    def start(self, name: str, *, parent: str | None = None, trace_id: str | None = None, attributes: dict | None = None) -> Span:
        parsed = parse_traceparent(parent)
        if parsed:
            trace_id, parent_span_id = parsed
        else:
            trace_id, parent_span_id = trace_id or _hex(16), ""
        return Span(
            tracer=self,
            name=name,
            trace_id=trace_id,
            span_id=_hex(8),
            parent_span_id=parent_span_id,
            attributes={**self.attributes, **(attributes or {})},
        )

    @contextmanager
    def span(self, name: str, **kwargs) -> Iterator[Span]:
        span = self.start(name, **kwargs)
        try:
            yield span
        except Exception as exc:  # noqa: BLE001
            span.end(error=f"{type(exc).__name__}: {exc}")
            raise
        else:
            span.end()

    # -- 导出 -------------------------------------------------------------- #
    def export(self, span: Span) -> None:
        if not self.enabled:
            return
        payload = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": self.service}},
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "mini-harness", "version": "0.1.0"},
                            "spans": [
                                {
                                    "traceId": span.trace_id,
                                    "spanId": span.span_id,
                                    "parentSpanId": span.parent_span_id,
                                    "name": span.name,
                                    "kind": span.kind,
                                    "startTimeUnixNano": str(span.start_ns),
                                    "endTimeUnixNano": str(span.end_ns),
                                    "attributes": _attributes(span.attributes),
                                    "status": {"code": span.status},
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.endpoint}/v1/traces",
            data=body,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                response.read()
            self.exported += 1
        except (urllib.error.URLError, TimeoutError, OSError):
            # 观测失败不能影响业务
            self.failed += 1


def _attributes(attrs: dict) -> list[dict]:
    out = []
    for key, value in attrs.items():
        if isinstance(value, bool):
            out.append({"key": key, "value": {"boolValue": value}})
        elif isinstance(value, int):
            out.append({"key": key, "value": {"intValue": str(value)}})
        else:
            out.append({"key": key, "value": {"stringValue": str(value)}})
    return out
