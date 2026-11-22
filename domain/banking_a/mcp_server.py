"""最小 MCP stdio server：暴露场景 A 的三个工具。

协议要点（MCP over stdio）：一行一条 JSON-RPC 2.0 消息；stdout 只写协议帧，
诊断写 stderr。实现 `initialize` / `tools/list` / `tools/call` / `ping` 即可被主流客户端调用。

为什么先做 MCP 而不是 DSH tool 包：领域工具要能被其他 Agent 复用，
进程隔离也更好做；需要在 DSH 内深度集成时再包一层 tool 包（见 docs/15 §3.5）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .extract import load_cases
from .knowledge import KnowledgeBase
from .rules import RuleSet, evaluate

DATA = Path(__file__).parent / "data"
SERVER_INFO = {"name": "banking-a-tools", "version": "0.1.0"}
PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "kb.search",
        "description": "检索法规与行内制度，返回条款级片段与引用 id（只能引用返回结果里的 cid）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索词，如「数据出境 安全评估」"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "doc.extract",
        "description": "从案例材料抽取结构化事实（流向/人数/敏感人数/重要数据/关基/豁免），并标记疑似注入文本。",
        "inputSchema": {
            "type": "object",
            "properties": {"case_id": {"type": "string", "description": "案例 id，如 CASE-01"}},
            "required": ["case_id"],
        },
    },
    {
        "name": "rule.evaluate",
        "description": "按规则集判定出境路径、所需材料与引用；阈值来自 rules/vN.json。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "case_id": {"type": "string"},
                "rules_version": {"type": "string", "enum": ["v1", "v2"], "default": "v1"},
            },
            "required": ["case_id"],
        },
    },
]


class Server:
    def __init__(self) -> None:
        self.kb = KnowledgeBase.load(DATA / "corpus")
        self.cases = load_cases(DATA / "cases" / "cases.md")
        self.rulesets = {name: RuleSet.load(DATA / "rules" / f"{name}.json") for name in ("v1", "v2")}

    def call(self, name: str, arguments: dict) -> dict:
        if name == "kb.search":
            return {"results": self.kb.search(arguments.get("query", ""), int(arguments.get("top_k", 5)))}
        if name == "doc.extract":
            case_id = arguments.get("case_id", "")
            if case_id not in self.cases:
                raise KeyError(f"unknown case: {case_id}")
            return self.cases[case_id].to_dict()
        if name == "rule.evaluate":
            case_id = arguments.get("case_id", "")
            if case_id not in self.cases:
                raise KeyError(f"unknown case: {case_id}")
            version = arguments.get("rules_version", "v1")
            if version not in self.rulesets:
                raise KeyError(f"unknown rules version: {version}")
            decision = evaluate(self.cases[case_id].facts, self.rulesets[version])
            valid, missing = self.kb.index.validate(decision.citations)
            payload = decision.to_dict()
            payload["citations_valid"] = valid
            payload["missing_citations"] = missing
            return payload
        raise KeyError(f"unknown tool: {name}")


def handle(server: Server, request: dict) -> dict | None:
    method = request.get("method")
    request_id = request.get("id")
    if request_id is None and method and method.startswith("notifications/"):
        return None

    if method == "initialize":
        return result(request_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })
    if method == "ping":
        return result(request_id, {})
    if method == "tools/list":
        return result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        params = request.get("params") or {}
        try:
            payload = server.call(params.get("name", ""), params.get("arguments") or {})
            return result(request_id, {
                "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                "isError": False,
            })
        except Exception as exc:  # noqa: BLE001 —— 工具错误要回到客户端，而不是断开连接
            return result(request_id, {
                "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                "isError": True,
            })
    return error(request_id, -32601, f"method not found: {method}")


def result(request_id, payload) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def main() -> int:
    # MCP 规定 UTF-8；Windows 下管道默认是 GBK，必须显式重配
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    server = Server()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle(server, request)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
