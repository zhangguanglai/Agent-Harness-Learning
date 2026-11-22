"""评估工作流：抽取 → 判定 → 引用校验 → 起草 → 审批 → 定稿。

一次 run 的事件序列（控制面与审计都消费这条流）：
  assessment.started
  extract.completed
  rules.evaluated
  citations.verified
  report.drafted
  artifact.drafted
  approval.requested          （需要审批时）
  approval.resolved
  artifact.finalized
  assessment.completed | assessment.rejected

审批是「等待」而不是「回调」：workflow 在 gate 上阻塞，HTTP 的 approve 负责放行。
"""

from __future__ import annotations

import json
from typing import Callable

from domain.banking_a.extract import load_cases
from domain.banking_a.knowledge import KnowledgeBase
from domain.banking_a.rules import RuleSet, evaluate

from .store import Store

APPROVAL_PATHS = {"security_assessment", "standard_contract", "exempt_scenario"}

PATH_TITLES = {
    "not_applicable": "不构成出境",
    "security_assessment": "申报数据出境安全评估",
    "standard_contract": "订立标准合同并备案（或保护认证）",
    "exempt_small": "免于申报（仍需告知同意）",
    "exempt_scenario": "场景豁免（需留存依据）",
}


def render_report(run_id: str, case_id: str, extraction, decision, citations: list[dict]) -> str:
    facts = extraction.facts
    lines = [
        f"# 跨境数据出境评估结论（{case_id}）",
        "",
        f"- 运行：`{run_id}`",
        f"- 规则版本：{decision.rule_version}",
        f"- 判定：**{PATH_TITLES.get(decision.path, decision.path)}**",
        "",
        "## 关键事实",
        "",
        "| 字段 | 值 | 依据（原始行） |",
        "| --- | --- | --- |",
    ]
    for key in ("destination", "subjects", "categories", "records", "sensitive_records", "important_data", "cii", "exemption"):
        value = facts.get(key)
        if isinstance(value, list):
            value = "、".join(value) if value else "—"
        evidence = extraction.evidence.get(key, "—")
        lines.append(f"| {key} | {value} | {evidence} |")

    lines += ["", "## 判定理由", ""] + [f"- {reason}" for reason in decision.reasons]
    lines += ["", "## 需要准备的材料", ""] + [f"- {item}" for item in decision.required_evidence]

    lines += ["", "## 引用依据", ""]
    for citation in citations:
        if citation["valid"]:
            lines.append(f"- `{citation['cid']}`：{citation['quote']}")
        else:
            lines.append(f"- `{citation['cid']}`：**无法定位（需人工复核）**")

    if extraction.warnings:
        lines += ["", "## 风险提示", ""] + [f"- {warning}" for warning in extraction.warnings]
    if extraction.injection_detected:
        lines += ["", "## 注入检测", "", "- 材料中出现疑似指令文本，已忽略其指令，仅按事实判定。"]

    lines += ["", "---", "", "> 本结论由工具链生成，需经合规人员审批后方可用于对外报送。"]
    return "\n".join(lines) + "\n"


class AssessmentWorkflow:
    def __init__(
        self,
        store: Store,
        *,
        corpus_dir,
        cases_file,
        rules_dir,
        approval_timeout_s: float = 600.0,
    ):
        self.store = store
        self.kb = KnowledgeBase.load(corpus_dir)
        self.cases = load_cases(cases_file)
        self.rulesets = {name: RuleSet.load(rules_dir / f"{name}.json") for name in ("v1", "v2")}
        self.approval_timeout_s = approval_timeout_s

    def run(
        self,
        run_id: str,
        case_id: str,
        *,
        rules_version: str = "v1",
        request_approval: Callable[[str, str, dict], tuple[bool, str, str]],
    ) -> None:
        store = self.store
        store.append_event(run_id, "assessment.started", {"case_id": case_id})

        extraction = self.cases[case_id]
        facts = extraction.facts
        store.append_event(
            run_id,
            "extract.completed",
            {
                "facts": facts,
                "warnings": extraction.warnings,
                "injection_detected": extraction.injection_detected,
            },
        )

        if rules_version not in self.rulesets:
            raise ValueError(f"未知规则版本：{rules_version}")
        decision = evaluate(facts, self.rulesets[rules_version])
        store.append_event(run_id, "rules.evaluated", decision.to_dict())

        valid, missing = self.kb.index.validate(decision.citations)
        citations = self.kb.index.resolve_quotes(decision.citations)
        store.append_event(
            run_id,
            "citations.verified",
            {"valid": valid, "missing": missing, "citations": citations},
        )
        if not valid:
            # 引用不合法直接失败：宁可不给结论，也不给一个编造的结论
            store.set_status(run_id, "failed", error=f"引用无法定位：{missing}")
            store.append_event(run_id, "assessment.failed", {"reason": "invalid_citations", "missing": missing})
            return

        report = render_report(run_id, case_id, extraction, decision, citations)
        store.append_event(run_id, "report.drafted", {"chars": len(report), "path": f"artifacts/{run_id}/report.md"})

        draft = store.write_artifact(run_id, "report.md", report, status="draft")
        store.write_artifact(run_id, "assessment.json", json.dumps(decision.to_dict(), ensure_ascii=False, indent=2), status="draft")
        store.append_event(run_id, "artifact.drafted", {"artifact_id": draft["id"], "version": draft["version"], "path": draft["path"]})

        needs_approval = decision.path in APPROVAL_PATHS
        if not needs_approval:
            final = store.write_artifact(run_id, "report.final.md", report, status="final")
            store.append_event(run_id, "artifact.finalized", {"artifact_id": final["id"], "version": final["version"]})
            store.set_status(run_id, "completed", decision=decision.to_dict())
            store.append_event(run_id, "assessment.completed", {"path": decision.path, "auto": True})
            return

        # 审批记录、状态切换与事件都由编排层统一处理（worker 审批也走同一个闸门）
        approved, by, comment = request_approval(run_id, "submit_assessment", {"path": decision.path})

        if not approved:
            store.set_status(run_id, "rejected", decision=decision.to_dict())
            store.append_event(run_id, "assessment.rejected", {"path": decision.path, "by": by})
            return

        final = store.write_artifact(run_id, "report.final.md", report, status="final")
        store.append_event(run_id, "artifact.finalized", {"artifact_id": final["id"], "version": final["version"], "by": by})
        store.set_status(run_id, "completed", decision=decision.to_dict())
        store.append_event(run_id, "assessment.completed", {"path": decision.path, "auto": False})
