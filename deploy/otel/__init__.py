"""S0-5：本地 OTLP 收集器。"""

from .collector import SpanStore, make_server

__all__ = ["SpanStore", "make_server"]
