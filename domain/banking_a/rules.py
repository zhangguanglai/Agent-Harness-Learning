"""规则引擎：判定是否出境、适用哪条路径、需要哪些材料。

规则与阈值放在 JSON 里（v1 / v2 两版），这样：
- 业务可以改阈值而不改代码；
- 评测可以对比两版规则下的判定差异（版本回归）；
- 每条结论都必须带规则集里登记的引用，不允许模型自由发挥。

⚠️ 阈值是**示例值**，上线前必须按最新法规与行内口径校准。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

PATH_LABELS = {
    "not_applicable": "不构成出境，不适用",
    "security_assessment": "申报数据出境安全评估",
    "standard_contract": "订立标准合同并备案（或保护认证）",
    "exempt_small": "免于申报（仍需告知同意）",
    "exempt_scenario": "场景豁免（需留存依据）",
}


@dataclass
class RuleSet:
    version: str
    thresholds: dict
    citations: dict[str, list[str]]
    evidence: dict[str, list[str]]
    exemptions: list[str]
    note: str = ""

    @classmethod
    def load(cls, path: str | Path) -> "RuleSet":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            version=payload["version"],
            thresholds=payload["thresholds"],
            citations=payload["citations"],
            evidence=payload["required_evidence"],
            exemptions=payload.get("exemptions", []),
            note=payload.get("note", ""),
        )


@dataclass
class Decision:
    path: str
    reasons: list[str]
    required_evidence: list[str]
    citations: list[str]
    rule_version: str

    @property
    def label(self) -> str:
        return PATH_LABELS.get(self.path, self.path)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "path_label": self.label,
            "reasons": self.reasons,
            "required_evidence": self.required_evidence,
            "citations": self.citations,
            "rule_version": self.rule_version,
        }


def evaluate(facts: dict, ruleset: RuleSet) -> Decision:
    t = ruleset.thresholds
    reasons: list[str] = []
    path: str

    if not facts.get("cross_border"):
        path = "not_applicable"
        reasons.append("数据流向为境内，未构成出境")
    elif facts.get("important_data"):
        path = "security_assessment"
        reasons.append("涉及重要数据，必须申报安全评估")
    elif facts.get("cii"):
        path = "security_assessment"
        reasons.append("主体属于关键信息基础设施运营者，必须申报安全评估")
    elif int(facts.get("sensitive_records") or 0) >= int(t["assessment_sensitive"]):
        path = "security_assessment"
        reasons.append(
            f"敏感个人信息 {facts['sensitive_records']} 人 ≥ 阈值 {t['assessment_sensitive']}，必须申报安全评估"
        )
    elif int(facts.get("records") or 0) >= int(t["assessment_pi"]):
        path = "security_assessment"
        reasons.append(f"个人信息 {facts['records']} 人 ≥ 阈值 {t['assessment_pi']}，必须申报安全评估")
    elif facts.get("exemption") and facts["exemption"] in ruleset.exemptions and int(facts.get("sensitive_records") or 0) == 0:
        path = "exempt_scenario"
        reasons.append(f"命中豁免场景「{facts['exemption']}」，且不含敏感个人信息")
    elif int(facts.get("records") or 0) > int(t["contract_pi"]):
        path = "standard_contract"
        reasons.append(
            f"个人信息 {facts['records']} 人 在 ({t['contract_pi']}, {t['assessment_pi']}) 区间，适用标准合同或认证"
        )
    else:
        path = "exempt_small"
        reasons.append(
            f"个人信息 {facts['records']} 人 ≤ 阈值 {t['contract_pi']}，且不含敏感个人信息与重要数据，免于申报"
        )

    return Decision(
        path=path,
        reasons=reasons,
        required_evidence=list(ruleset.evidence.get(path, [])),
        citations=list(ruleset.citations.get(path, [])),
        rule_version=ruleset.version,
    )
