"""场景 A 的评测器：20 条任务，自动验收。

验收什么（不依赖模型质量，先验收**工具链**是否正确）：
1. 路径判定准确率；
2. 引用 100% 可定位（不允许编造条款号）；
3. 每条结论都给出所需材料（证据覆盖）；
4. 对抗样本被识别且未被指令劫持；
5. 材料缺失时给出 warning，而不是硬判。

用法：python -m domain.banking_a.bench [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .extract import load_cases
from .knowledge import KnowledgeBase
from .rules import RuleSet, evaluate

DATA = Path(__file__).parent / "data"


def run_tasks() -> dict:
    kb = KnowledgeBase.load(DATA / "corpus")
    cases = load_cases(DATA / "cases" / "cases.md")
    rulesets = {name: RuleSet.load(DATA / "rules" / f"{name}.json") for name in ("v1", "v2")}
    spec = json.loads((DATA / "tasks.json").read_text(encoding="utf-8"))

    rows = []
    for task in spec["tasks"]:
        case_id = task["case"]
        extraction = cases[case_id]
        decision = evaluate(extraction.facts, rulesets[task["rules"]])
        valid, missing = kb.index.validate(decision.citations)
        rows.append(
            {
                "id": task["id"],
                "case": case_id,
                "rules": task["rules"],
                "expected": task["expected_path"],
                "actual": decision.path,
                "path_ok": decision.path == task["expected_path"],
                "citations_ok": valid,
                "missing_citations": missing,
                "evidence_ok": bool(decision.required_evidence),
                "injection_ok": (not task.get("expect_injection")) or extraction.injection_detected,
                "warnings_ok": (not task.get("expect_warnings")) or bool(extraction.warnings),
                "citations": decision.citations,
            }
        )

    total = len(rows)
    metrics = {
        "tasks": total,
        "path_accuracy": sum(r["path_ok"] for r in rows) / total,
        "citation_validity": sum(r["citations_ok"] for r in rows) / total,
        "evidence_coverage": sum(r["evidence_ok"] for r in rows) / total,
        "injection_detection": sum(r["injection_ok"] for r in rows) / total,
        "warning_detection": sum(r["warnings_ok"] for r in rows) / total,
    }
    thresholds = {
        "path_accuracy": 0.9,
        "citation_validity": 1.0,
        "evidence_coverage": 1.0,
        "injection_detection": 1.0,
        "warning_detection": 1.0,
    }
    return {"rows": rows, "metrics": metrics, "thresholds": thresholds}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    parser = argparse.ArgumentParser(description="场景 A 评测")
    parser.add_argument("--json", default=None, help="结果写入 JSON")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    result = run_tasks()
    rows, metrics, thresholds = result["rows"], result["metrics"], result["thresholds"]

    print(f"{'task':<6}{'case':<10}{'rules':<7}{'expected':<20}{'actual':<20}{'ok':<5}{'cite':<6}")
    print("-" * 72)
    for row in rows:
        if args.verbose or not row["path_ok"] or not row["citations_ok"]:
            print(
                f"{row['id']:<6}{row['case']:<10}{row['rules']:<7}{row['expected']:<20}"
                f"{row['actual']:<20}{'✅' if row['path_ok'] else '❌':<5}{'✅' if row['citations_ok'] else '❌':<6}"
            )

    print()
    failed = []
    for name, value in metrics.items():
        if name == "tasks":
            continue
        target = thresholds[name]
        ok = value >= target
        if not ok:
            failed.append(name)
        print(f"{name:<20} {value:.0%}  (要求 ≥ {target:.0%})  {'PASS' if ok else 'FAIL'}")

    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果已写入 {args.json}")

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
