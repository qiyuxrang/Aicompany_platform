"""Deterministic tender notice normalization."""

from __future__ import annotations

import html as html_module
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Iterable
from urllib.parse import urljoin

STATUS_OK = "OK"
STATUS_UNKNOWN = "UNKNOWN"
BEIJING = timezone(timedelta(hours=8))

FIELD_SPECS: dict[str, tuple[str, ...]] = {
    "project_name": ("项目名称", "采购项目名称", "招标项目名称", "工程名称", "项目名"),
    "project_code": ("项目编号", "采购项目编号", "招标编号", "采购编号", "项目代码", "标段编号"),
    "notice_type": ("公告类型", "公告种类", "公告性质"),
    "purchaser": ("采购人名称", "采购单位", "采购人", "招标人", "招标单位", "建设单位"),
    "agency": ("代理机构名称", "采购代理机构", "招标代理机构", "代理机构"),
    "region": ("项目所在地", "所属地区", "行政区域", "行政区划", "所在地区", "地区"),
    "publish_at": ("公告发布时间", "公告时间", "发布时间", "发布日期"),
    "signup_time": ("报名时间", "获取采购文件时间", "获取招标文件时间", "文件获取时间", "报名及获取文件时间"),
    "bid_deadline": ("投标截止时间", "递交投标文件截止时间", "响应文件递交截止时间", "报价截止时间", "截止时间"),
    "bid_open_at": ("开标时间", "开启时间", "唱标时间"),
    "budget": ("预算金额", "采购预算", "预算总额", "项目预算", "预算"),
    "budget_cap": ("最高限价", "最高投标限价", "控制价", "拦标价"),
    "procurement_method": ("采购方式", "招标方式", "采购形式"),
    "contact_person": ("项目联系人", "联系人"),
    "contact_phone": ("联系电话", "联系方式", "电话", "咨询电话"),
}

_DATETIME_FIELDS = frozenset({"publish_at", "signup_time", "bid_deadline", "bid_open_at"})
_AMOUNT_FIELDS = frozenset({"budget", "budget_cap"})
_NOTICE_TYPE_KEYWORDS = (
    "中标结果公告", "公开招标公告", "竞争性磋商公告", "竞争性谈判公告", "资格预审公告",
    "中标公告", "成交公告", "废标公告", "流标公告", "更正公告", "变更公告",
    "澄清公告", "补充公告", "延期公告", "询价公告", "单一来源公告", "招标公告", "采购公告",
)
_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
_BLOCK_RE = re.compile(r"</?(?:p|div|tr|br|li|h[1-6]|table|td|th)[^>]*>", re.I)
_ATTACHMENT_RE = re.compile(
    r'<a\s[^>]*href=["\']([^"\']+?\.(?:pdf|docx?|xlsx?|zip|rar|7z))(?:\?[^"\']*)?["\'][^>]*>(.*?)</a>',
    re.I | re.S,
)
_DATE_RE = re.compile(
    r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})(?:\s*日)?"
    r"(?:\s*[T ]?\s*(\d{1,2})\s*[:：时]\s*(\d{1,2})(?:\s*[:：分]\s*(\d{1,2}))?\s*(?:秒)?)?"
)


@dataclass
class FieldResult:
    name: str
    value: str | None = None
    raw: str = ""
    status: str = STATUS_UNKNOWN
    rule: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        payload = {"value": self.value, "raw": self.raw, "status": self.status, "rule": self.rule}
        payload.update(self.extra)
        return payload


@dataclass
class NormalizedNotice:
    fields: dict[str, FieldResult]
    attachments: list[dict] = field(default_factory=list)
    source_ref: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def value_of(self, name: str) -> str | None:
        item = self.fields.get(name)
        return item.value if item else None

    def status_of(self, name: str) -> str:
        item = self.fields.get(name)
        return item.status if item else STATUS_UNKNOWN

    @property
    def unknown_fields(self) -> list[str]:
        return sorted(name for name, item in self.fields.items() if item.status == STATUS_UNKNOWN)

    @property
    def known_fields(self) -> list[str]:
        return sorted(name for name, item in self.fields.items() if item.status == STATUS_OK)

    def to_dict(self) -> dict:
        return {
            "fields": {name: item.to_dict() for name, item in self.fields.items()},
            "attachments": self.attachments,
            "source_ref": self.source_ref,
            "warnings": self.warnings,
            "known_fields": self.known_fields,
            "unknown_fields": self.unknown_fields,
        }


@dataclass(frozen=True)
class PublicationEvidence:
    publish_at: datetime | None = None
    publish_date: date | None = None
    precision: str = "unknown"
    raw: str = ""
    provenance: str = ""
    reason: str = "publish_date_missing"

    @property
    def verified(self) -> bool:
        return self.publish_date is not None and self.precision != "unknown"


def html_to_lines(raw: bytes | str, *, encoding: str = "utf-8") -> list[str]:
    text = raw.decode(encoding, errors="replace") if isinstance(raw, bytes) else raw
    text = _TAG_RE.sub(" ", text)
    text = _BLOCK_RE.sub("\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_module.unescape(text).replace("\u3000", " ").replace("\xa0", " ")
    return [cleaned for chunk in text.splitlines() if (cleaned := re.sub(r"[ \t]+", " ", chunk).strip())]


def _parse_datetime_parts(text: str) -> tuple[str, str] | None:
    if not text:
        return None
    match = _DATE_RE.search(str(text))
    if not match:
        return None
    try:
        moment = datetime(
            int(match.group(1)), int(match.group(2)), int(match.group(3)),
            int(match.group(4) or 0), int(match.group(5) or 0), int(match.group(6) or 0),
            tzinfo=BEIJING,
        )
    except ValueError:
        return None
    precision = "second" if match.group(6) else "minute" if match.group(4) else "date"
    return moment.isoformat(), precision


def parse_datetime(text: str) -> str | None:
    parsed = _parse_datetime_parts(text)
    return parsed[0] if parsed else None


def parse_amount(text: str) -> dict | None:
    if not text:
        return None
    cleaned = str(text).replace(",", "").replace("，", "").replace(" ", "")
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(亿元|亿|万元|万|元)?", cleaned)
    if not match:
        return None
    try:
        number = Decimal(match.group(1))
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    unit = match.group(2) or ""
    multipliers = {"亿元": Decimal(100000000), "亿": Decimal(100000000),
                   "万元": Decimal(10000), "万": Decimal(10000), "元": Decimal(1)}
    yuan = (number * multipliers[unit]).quantize(Decimal("1")) if unit in multipliers else None
    return {"raw": str(text).strip(), "number": str(number), "unit": unit,
            "amount_yuan": str(yuan) if yuan is not None else None, "currency": "CNY"}


def _match_label_value(lines: list[str], synonyms: Iterable[str]) -> tuple[str | None, str, str]:
    for index, line in enumerate(lines):
        for synonym in synonyms:
            position = line.find(synonym)
            if position < 0:
                continue
            tail = line[position + len(synonym):].lstrip("  :：=-—·*　")
            if tail:
                return tail.strip(), f"inline:{synonym}", line[:120]
            for offset in (1, 2):
                if index + offset < len(lines):
                    candidate = lines[index + offset].strip()
                    if candidate and not any(other in candidate for other in synonyms):
                        return candidate, f"nextline:{synonym}", line[:120]
    return None, "", ""


def _notice_type(lines: list[str], title: str) -> tuple[str | None, str, str]:
    value, rule, raw = _match_label_value(lines, FIELD_SPECS["notice_type"])
    if value:
        return value, rule, raw
    for keyword in sorted(_NOTICE_TYPE_KEYWORDS, key=len, reverse=True):
        if keyword in title:
            return keyword, f"title_keyword:{keyword}", title[:120]
    return None, "", ""


def normalize_notice(raw_bytes: bytes | str, *, source_code: str, original_url: str,
                     fallback_title: str = "", encoding: str = "utf-8") -> NormalizedNotice:
    html_text = raw_bytes.decode(encoding, errors="replace") if isinstance(raw_bytes, bytes) else raw_bytes
    if source_code == "sx_jk_ecai":
        html_text = re.sub(
            r"<script>\s*document\.write\s*\(\s*\(?\s*['\"](\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d)(?:\.0)?['\"]\s*\)?(?:\.replace\(['\"]T['\"],['\"] ['\"]\))?(?:\.substring\(0,\s*\d+\))?\s*\)\s*;?\s*</script>",
            lambda match: match.group(1), html_text,
        )
    if source_code == "shxjkjt":
        header_date = re.search(r'<div class="[^"]*u-m-r-15[^\"]*"[^>]*>时间：\s*(20\d{2}-\d\d-\d\d)</div>', html_text)
        if header_date:
            html_text = html_text.replace(header_date.group(0), f"<p>发布时间：{header_date.group(1)}</p>", 1)

    lines = html_to_lines(html_text)
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.I | re.S)
    title = re.sub(r"\s+", " ", html_module.unescape(re.sub(r"<[^>]+>", "", title_match.group(1)))).strip() if title_match else ""
    title = title or fallback_title
    fields: dict[str, FieldResult] = {}
    warnings: list[str] = []

    for name, synonyms in FIELD_SPECS.items():
        if source_code in {"shxjkjt", "csg_bidding"} and name == "purchaser":
            direct = re.search(r"招标人为\s*([^，。；;]{2,100}?(?:有限责任公司|股份有限公司|有限公司|集团公司|集团))(?=\s*[，。；;])", " ".join(lines))
            if direct:
                fields[name] = FieldResult(name, direct.group(1), direct.group(0), STATUS_OK,
                                           f"{source_code}:招标人为")
                continue
        if source_code == "csg_bidding" and name == "region":
            fields[name] = FieldResult(name)
            continue
        value, rule, raw = _notice_type(lines, title) if name == "notice_type" else _match_label_value(lines, synonyms)
        if not value:
            fields[name] = FieldResult(name, raw=raw)
            continue
        if name in _DATETIME_FIELDS:
            parsed = _parse_datetime_parts(value)
            if not parsed:
                fields[name] = FieldResult(name, raw=value, rule=f"{rule}|unparsable_datetime")
                warnings.append(f"{name}: 命中标签但时间格式无法解析")
                continue
            iso_value, precision = parsed
            stored_value = iso_value[:10] if name == "publish_at" and precision == "date" else iso_value
            fields[name] = FieldResult(name, stored_value, value, STATUS_OK, rule,
                                       {"precision": precision, "date": iso_value[:10]})
        elif name in _AMOUNT_FIELDS:
            parsed_amount = parse_amount(value)
            if not parsed_amount:
                fields[name] = FieldResult(name, raw=value, rule=f"{rule}|unparsable_amount")
                warnings.append(f"{name}: 命中标签但金额格式无法解析")
                continue
            fields[name] = FieldResult(name, value, value, STATUS_OK, rule, parsed_amount)
        else:
            fields[name] = FieldResult(name, value, value, STATUS_OK, rule)

    if fields["project_name"].status == STATUS_UNKNOWN and title:
        fields["project_name"] = FieldResult("project_name", title, title, STATUS_OK, "fallback:page_title")

    attachments, seen = [], set()
    for match in _ATTACHMENT_RE.finditer(html_text):
        href = match.group(1).strip()
        url = urljoin(original_url, href)
        if url in seen:
            continue
        seen.add(url)
        label = re.sub(r"\s+", " ", html_module.unescape(re.sub(r"<[^>]+>", "", match.group(2)))).strip()
        attachments.append({"url": url, "href": href, "label": label[:200]})

    return NormalizedNotice(fields, attachments,
                            {"source_code": source_code, "original_url": original_url, "title": title},
                            warnings)


def publication_evidence(normalized: NormalizedNotice, *, source_metadata: dict | None = None) -> PublicationEvidence:
    """Return verified publication date/precision without inventing a time."""
    source_metadata = source_metadata or {}
    candidates: list[PublicationEvidence] = []
    field = normalized.fields.get("publish_at")
    if field and field.status == STATUS_OK and field.value:
        parsed = _parse_datetime_parts(field.raw or field.value)
        if parsed:
            moment = datetime.fromisoformat(parsed[0])
            candidates.append(PublicationEvidence(
                moment if parsed[1] != "date" else None, moment.date(), parsed[1], field.raw,
                "detail:publish_at", "",
            ))

    source_code = normalized.source_ref.get("source_code", "")
    metadata_keys = ["detail_published_at"]
    if source_code == "ccgp_national":
        metadata_keys.append("list_published_at")
    for key in metadata_keys:
        raw = str(source_metadata.get(key) or "").strip()
        parsed = _parse_datetime_parts(raw)
        if not parsed:
            continue
        moment = datetime.fromisoformat(parsed[0])
        candidates.append(PublicationEvidence(
            moment if parsed[1] != "date" else None, moment.date(), parsed[1], raw,
            f"source_metadata:{key}", "",
        ))

    if not candidates:
        return PublicationEvidence()
    if len({item.publish_date for item in candidates}) != 1:
        return PublicationEvidence(reason="publish_date_conflict")
    rank = {"date": 1, "minute": 2, "second": 3}
    return max(candidates, key=lambda item: rank.get(item.precision, 0))


def extract_verified_publication(raw_bytes: bytes | str, *, source_code: str, original_url: str,
                                 fallback_title: str = "",
                                 source_metadata: dict | None = None) -> PublicationEvidence:
    """Extract publication evidence directly from fetched detail bytes."""
    normalized = normalize_notice(
        raw_bytes,
        source_code=source_code,
        original_url=original_url,
        fallback_title=fallback_title,
    )
    return publication_evidence(normalized, source_metadata=source_metadata)


__all__ = [
    "FIELD_SPECS", "FieldResult", "NormalizedNotice", "PublicationEvidence", "STATUS_OK",
    "STATUS_UNKNOWN", "extract_verified_publication", "html_to_lines", "normalize_notice",
    "parse_amount", "parse_datetime", "publication_evidence",
]
