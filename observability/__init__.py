"""可观测性：OTLP/HTTP JSON 追踪（标准库，零依赖）。"""

from .otel import Span, Tracer, parse_traceparent

__all__ = ["Tracer", "Span", "parse_traceparent"]
