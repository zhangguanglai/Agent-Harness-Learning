"""实验 11：场景 A 工具链 —— MCP 协议 + 引用校验 + 20 条评测。

验证：
1. `python -m domain.banking_a.bench` 20 条任务全部通过；
2. MCP stdio server 能被 initialize / tools/list / tools/call 驱动；
3. 工具输出带 citation id，且全部可定位（零编造）；
4. 对抗样本（文档内嵌指令）被识别，不改变判定；
5. 材料缺失时返回 warning。

运行：python labs/lab11_domain_a.py
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import json
import subprocess
import sys

from domain.banking_a.bench import main as bench_main


def call_mcp(requests: list[dict]) -> list[dict]:
    payload = "\n".join(json.dumps(item, ensure_ascii=False) for item in requests) + "\n"
    proc = subprocess.run(
        [sys.executable, "-m", "domain.banking_a.mcp_server"],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def run() -> None:
    # ---- 1. 评测集 -------------------------------------------------------- #
    code = bench_main([])
    assert code == 0, "评测未通过"
    print("✓ 评测：20 条任务全部通过（路径/引用/证据/注入/缺失 五项指标）")

    # ---- 2. MCP 协议 ------------------------------------------------------ #
    responses = call_mcp(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "kb.search", "arguments": {"query": "安全评估 标准合同", "top_k": 3}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "rule.evaluate", "arguments": {"case_id": "CASE-04", "rules_version": "v1"}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
             "params": {"name": "doc.extract", "arguments": {"case_id": "CASE-10"}}},
        ]
    )
    by_id = {item["id"]: item for item in responses}
    assert by_id[1]["result"]["serverInfo"]["name"] == "banking-a-tools"
    tools = by_id[2]["result"]["tools"]
    assert {tool["name"] for tool in tools} == {"kb.search", "doc.extract", "rule.evaluate"}
    print("✓ MCP：initialize 与 tools/list 正常，三个工具都在")

    search = json.loads(by_id[3]["result"]["content"][0]["text"])
    assert search["results"], "检索无结果"
    assert all("#" in hit["cid"] for hit in search["results"]), "检索结果必须带 citation id"
    print(f"✓ 检索：返回 {len(search['results'])} 条，全部带 cid（如 {search['results'][0]['cid']}）")

    decision = json.loads(by_id[4]["result"]["content"][0]["text"])
    assert decision["path"] == "security_assessment", decision["path"]
    assert decision["citations_valid"] is True, decision["missing_citations"]
    assert decision["required_evidence"], "必须给出所需材料"
    print("✓ 判定：CASE-04 → 安全评估，引用全部可定位")

    extraction = json.loads(by_id[5]["result"]["content"][0]["text"])
    assert extraction["injection_detected"] is True
    assert extraction["facts"]["records"] == 80_000
    print("✓ 注入：CASE-10 的「忽略指令」被标记，事实仍按原文抽取")

    print("\n结论：领域工具的价值不在「回答得像」，而在「每条结论都能落到条款」。")


if __name__ == "__main__":
    run()
    print("\nLAB11 PASS")
