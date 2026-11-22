"""从案例材料里抽取事实（离线规则版）。

生产用 LLM + schema 约束；这里用「标签行解析」保证确定性，方便做评测基线。
两个安全点：
1. 文档里的文字只当**数据**：出现「忽略指令」这类内容只记录、不执行；
2. 每个字段都保留原始行，便于人工复核与引用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

CN_NUM = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

INJECTION_PATTERNS = ("忽略以上", "忽略之前", "ignore previous", "ignore all previous", "系统指令")

FIELD_ALIASES = {
    "业务场景": "purpose",
    "场景": "purpose",
    "数据流向": "destination",
    "流向": "destination",
    "涉及主体": "subjects",
    "数据主体": "subjects",
    "数据类别": "categories",
    "数据类型": "categories",
    "数据量": "records",
    "人数": "records",
    "涉及人数": "records",
    "敏感个人信息": "sensitive_records",
    "敏感数据量": "sensitive_records",
    "重要数据": "important_data",
    "是否重要数据": "important_data",
    "关键信息基础设施": "cii",
    "是否关基": "cii",
    "豁免依据": "exemption",
    "豁免": "exemption",
    "接收方": "recipient",
    "备注": "notes",
}

OUTBOUND_HINTS = ("香港", "澳门", "台湾", "境外", "海外", "新加坡", "伦敦", "纽约")


@dataclass
class Extraction:
    facts: dict = field(default_factory=dict)
    evidence: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    injection_detected: bool = False

    def to_dict(self) -> dict:
        return {
            "facts": self.facts,
            "evidence": self.evidence,
            "warnings": self.warnings,
            "injection_detected": self.injection_detected,
        }


def _to_int(raw: str) -> int | None:
    cleaned = raw.replace(",", "").replace("，", "")
    match = re.search(r"\d+", cleaned)
    if match:
        return int(match.group())
    return None


def _to_bool(raw: str) -> bool | None:
    if any(word in raw for word in ("是", "yes", "true", "属于")):
        return True
    if any(word in raw for word in ("否", "no", "false", "不属于")):
        return False
    return None


def extract_case(path: str | Path) -> Extraction:
    return extract_text(Path(path).read_text(encoding="utf-8"))


def extract_text(text: str) -> Extraction:
    result = Extraction()

    for line in text.splitlines():
        stripped = line.strip().lstrip("-").strip()
        if not stripped or stripped.startswith("#"):
            continue

        if any(pattern in stripped.lower() for pattern in INJECTION_PATTERNS):
            result.injection_detected = True
            result.warnings.append(f"检测到疑似注入文本：{stripped[:60]}")
            continue

        match = re.match(r"^([^：:]{2,12})[：:]\s*(.+)$", stripped)
        if not match:
            continue
        label, value = match.group(1).strip(), match.group(2).strip()
        field_name = FIELD_ALIASES.get(label)
        if not field_name:
            continue

        result.evidence[field_name] = stripped
        if field_name in {"records", "sensitive_records"}:
            number = _to_int(value)
            result.facts[field_name] = number if number is not None else 0
            if number is None:
                result.warnings.append(f"无法解析人数：{label}={value}")
        elif field_name in {"important_data", "cii"}:
            result.facts[field_name] = bool(_to_bool(value))
        elif field_name == "categories":
            result.facts[field_name] = [item.strip() for item in re.split(r"[、,，/]", value) if item.strip()]
        else:
            result.facts[field_name] = value

    facts = result.facts
    parsed_fields = set(facts)
    facts.setdefault("records", 0)
    facts.setdefault("sensitive_records", 0)
    facts.setdefault("important_data", False)
    facts.setdefault("cii", False)
    facts.setdefault("categories", [])
    facts.setdefault("exemption", None)
    if facts.get("exemption") in {"无", "none", "None", ""}:
        facts["exemption"] = None

    destination = str(facts.get("destination", ""))
    facts["cross_border"] = any(hint in destination for hint in OUTBOUND_HINTS) or "出境" in destination
    facts.setdefault("purpose", "")

    missing = [key for key in ("destination", "records") if key not in parsed_fields]
    if missing:
        result.warnings.append(f"材料缺少关键字段：{', '.join(missing)}")
    return result


def load_cases(path: str | Path) -> dict[str, Extraction]:
    """一个 Markdown 文件里用 `# CASE-xx 标题` 分隔多个案例。"""
    text = Path(path).read_text(encoding="utf-8")
    blocks = re.split(r"^#\s+(CASE-[0-9A-Za-z-]+).*$", text, flags=re.MULTILINE)
    cases: dict[str, Extraction] = {}
    for index in range(1, len(blocks), 2):
        case_id = blocks[index]
        body = blocks[index + 1] if index + 1 < len(blocks) else ""
        cases[case_id] = extract_text(body)
    return cases
