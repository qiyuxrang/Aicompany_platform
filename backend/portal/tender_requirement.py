"""Safe, offline qualification requirement rules for tender notices."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping

from .tender_dedupe import extract_requirement_blocks

ENTERPRISE_QUALIFICATION = "ENTERPRISE_QUALIFICATION"
PERSONNEL_CERTIFICATE = "PERSONNEL_CERTIFICATE"
PROJECT_PERFORMANCE = "PROJECT_PERFORMANCE"
FINANCIAL_CONDITION = "FINANCIAL_CONDITION"
CREDIT_CONDITION = "CREDIT_CONDITION"
MANUFACTURER_AUTHORIZATION = "MANUFACTURER_AUTHORIZATION"
OTHER_ADMISSION_MATERIAL = "OTHER_ADMISSION_MATERIAL"

SATISFIED = "SATISFIED"
UNSATISFIED = "UNSATISFIED"
PENDING = "PENDING"
RULE_VERSION = "5A.v1"

_CATEGORY_KEYWORDS = (
    (MANUFACTURER_AUTHORIZATION, ("厂家授权", "制造商授权", "原厂授权", "授权书")),
    (FINANCIAL_CONDITION, ("财务", "审计报告", "资产负债", "营业收入")),
    (CREDIT_CONDITION, ("信用", "失信", "信用中国", "政府采购严重违法")),
    (PROJECT_PERFORMANCE, ("业绩", "类似项目", "同类项目", "合同金额", "履约")),
    (PERSONNEL_CERTIFICATE, ("建造师", "项目经理", "技术负责人", "职称", "人员证书")),
    (ENTERPRISE_QUALIFICATION, (
        "资质", "许可证", "施工总承包", "专业承包", "设计资质", "体系认证",
        "iso", "cmmi", "itss",
    )),
)
_SUPPORTED_CATEGORIES = frozenset({ENTERPRISE_QUALIFICATION, PERSONNEL_CERTIFICATE})
_LEVEL_SERIES = (
    ("三级", "二级", "一级", "特级"),
    ("丁级", "丙级", "乙级", "甲级"),
    ("丙等", "乙等", "甲等"),
    ("C级", "B级", "A级"),
)
_LEVEL_RE = re.compile(r"特级|[一二三四]级|[甲乙丙丁]级|[甲乙丙]等|[ABC]级")
_SENTENCE_SPLIT_RE = re.compile(r"[；;。\n]+")
_NUMBER_PREFIX_RE = re.compile(r"^\s*(?:\d+[.、)）]|[一二三四五六七八九十]+[、)])\s*")
_LEADING_REQUIREMENT_RE = re.compile(
    r"^.*?(?:须具备|应具备|须具有|应具有|必须具备|必须具有|须提供|应提供)"
)
_PUNCT_RE = re.compile(r"[\s\u3000()（）\[\]【】《》<>,，.。;；:：\-—_/\\|·]+")
_TRAILING_QUALIFIER_RE = re.compile(r"(?:及以上|或以上|以上|及更高级别|及更高级|[（(]含[)）])+$")


@dataclass(frozen=True)
class Requirement:
    text: str
    category: str
    target: str = ""
    level: str = ""
    section: str = ""
    rule_version: str = RULE_VERSION


@dataclass(frozen=True)
class Evidence:
    """Caller-supplied evidence facts; this module never fetches enterprise data."""

    category: str
    target: str
    level: str = ""
    verified: bool = False
    applicable: bool = False
    current: bool = False


@dataclass(frozen=True)
class Assessment:
    requirement: Requirement
    verdict: str
    reason: str
    evidence: Evidence | None = None


def classify_sentence(sentence: str) -> str:
    lowered = (sentence or "").lower()
    for category, keywords in _CATEGORY_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return category
    return OTHER_ADMISSION_MATERIAL


def level_series(level: str | None) -> int | None:
    cleaned = (level or "").strip()
    return next((index for index, levels in enumerate(_LEVEL_SERIES) if cleaned in levels), None)


def levels_comparable(left: str | None, right: str | None) -> bool:
    series = level_series(left)
    return series is not None and series == level_series(right)


def level_rank(level: str | None) -> int:
    series = level_series(level)
    if series is None:
        return -1
    return _LEVEL_SERIES[series].index((level or "").strip())


def _target(sentence: str, level: str) -> str:
    body = _LEADING_REQUIREMENT_RE.sub("", sentence, count=1).strip(" \t:：,，")
    if level:
        before, _, after = body.partition(level)
        body = before or after
    body = _TRAILING_QUALIFIER_RE.sub("", body)
    return body.strip(" \t:：,，;；。、")


def requirements_from_blocks(blocks: Mapping[str, str]) -> list[Requirement]:
    requirements: list[Requirement] = []
    seen: set[tuple[str, str]] = set()
    for section, block in blocks.items():
        for raw_sentence in _SENTENCE_SPLIT_RE.split(block or ""):
            sentence = _NUMBER_PREFIX_RE.sub("", raw_sentence.strip())
            if len(sentence) < 4:
                continue
            category = classify_sentence(sentence)
            level_match = _LEVEL_RE.search(sentence)
            level = level_match.group(0) if level_match else ""
            requirement = Requirement(
                text=sentence,
                category=category,
                target=_target(sentence, level) if category in _SUPPORTED_CATEGORIES else "",
                level=level,
                section=section,
            )
            key = (section, sentence)
            if key not in seen:
                seen.add(key)
                requirements.append(requirement)
    return requirements


def extract_requirements(lines: Iterable[str]) -> list[Requirement]:
    """Extract current requirement blocks, then classify their sentences offline."""

    return requirements_from_blocks(extract_requirement_blocks(lines))


def _normalise_target(value: str) -> str:
    return _PUNCT_RE.sub("", value or "").lower()


def assess_requirement(requirement: Requirement, evidence: Iterable[Evidence] = ()) -> Assessment:
    """Apply only exact-name, verified, applicable, current evidence rules."""

    if requirement.category not in _SUPPORTED_CATEGORIES or not requirement.target:
        return Assessment(requirement, PENDING, "当前离线规则不支持自动判定，待确认。")

    target = _normalise_target(requirement.target)
    matches = [
        item for item in evidence
        if item.category == requirement.category
        and _normalise_target(item.target) == target
    ]
    candidates = [item for item in matches if item.verified and item.applicable and item.current]
    if not candidates:
        return Assessment(requirement, PENDING, "缺少已核实、适用且当前有效的证据，待确认。")

    if not requirement.level:
        return Assessment(requirement, SATISFIED, "已核实的适用证据与要求精确对应。", candidates[0])

    comparable = [item for item in candidates if levels_comparable(requirement.level, item.level)]
    if not comparable or len(comparable) != len(candidates):
        return Assessment(requirement, PENDING, "证据等级缺失或等级体系不可比，待确认。")

    required_rank = level_rank(requirement.level)
    sufficient = [item for item in comparable if level_rank(item.level) >= required_rank]
    if sufficient:
        return Assessment(requirement, SATISFIED, "已核实证据的同体系等级达到要求。", sufficient[0])
    if len(candidates) != len(matches):
        return Assessment(requirement, PENDING, "同名证据仍有未核实、未确认适用或已失效记录，待确认。")

    strongest = max(comparable, key=lambda item: level_rank(item.level))
    return Assessment(requirement, UNSATISFIED, "已核实证据的同体系等级低于要求。", strongest)


def assess_requirements(
    requirements: Iterable[Requirement], evidence: Iterable[Evidence] = (),
) -> list[Assessment]:
    evidence_items = tuple(evidence)
    return [assess_requirement(requirement, evidence_items) for requirement in requirements]


__all__ = [
    "Assessment", "CREDIT_CONDITION", "ENTERPRISE_QUALIFICATION", "Evidence",
    "FINANCIAL_CONDITION", "MANUFACTURER_AUTHORIZATION", "OTHER_ADMISSION_MATERIAL",
    "PENDING", "PERSONNEL_CERTIFICATE", "PROJECT_PERFORMANCE", "RULE_VERSION",
    "Requirement", "SATISFIED", "UNSATISFIED", "assess_requirement",
    "assess_requirements", "classify_sentence", "extract_requirements", "level_rank",
    "level_series", "levels_comparable", "requirements_from_blocks",
]
