"""Pure helpers for source-local dedupe, versions, and opportunity events."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable

from .tender_normalize import NormalizedNotice

CORE_FIELDS = (
    "project_name", "project_code", "notice_type", "purchaser", "agency", "region",
    "publish_at", "signup_time", "bid_deadline", "bid_open_at", "budget", "budget_cap",
    "procurement_method", "contact_person", "contact_phone",
)
EVENT_TYPES = (
    "DISCOVERED", "NOTICE_UPDATED", "DEADLINE_CHANGED", "DOCUMENT_ADDED",
    "BUDGET_CHANGED", "REQUIREMENT_CHANGED", "AWARD_PUBLISHED",
)
SOURCE_HEALTH_EVENT_TYPES = (
    "PREFLIGHT_OK", "PREFLIGHT_BLOCKED", "FETCH_FAILED", "RATE_LIMITED", "DEGRADED", "RECOVERED",
)
_DATETIME_FIELDS = frozenset({"publish_at", "signup_time", "bid_deadline", "bid_open_at"})
_PUNCT_RE = re.compile(r"[\s\u3000()（）\[\]【】《》<>\"'`:：;；,，.。!！?？\-—_/\\|·]+")
_REQUIREMENT_HEADINGS = (
    "资格要求", "资质要求", "供应商资格", "申请人资格", "技术要求", "商务要求",
    "评审办法", "评标办法", "评分标准", "响应要求", "服务要求",
)
_SECTION_MARKER_RE = re.compile(
    r"^\s*(?:[（(]?\s*[一二三四五六七八九十]+\s*[)）]?[、.．]|第[一二三四五六七八九十]+[章节条])"
)
_AWARD_KEYWORDS = ("中标", "成交", "废标", "流标", "终止")


def canonical_datetime(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return value
    return parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo else value


def normalise_text(value: str | None) -> str:
    if not value:
        return ""
    return _PUNCT_RE.sub("", str(value).strip().lower()
                         .replace("（", "(").replace("）", ")").replace("：", ":"))


def canonical_key(source_code: str, project_code: str | None, project_name: str | None,
                  source_notice_id: str = "") -> str:
    code = normalise_text(project_code)
    if code:
        return f"{source_code}:code:{code}"
    name = normalise_text(project_name)
    if name:
        return f"{source_code}:name:{name}"
    return f"{source_code}:nid:{normalise_text(source_notice_id)}"


def possible_match_keys(source_code: str, canonical: str, others: Iterable[str]) -> list[str]:
    return sorted({key for key in others if key and key != canonical and not key.startswith(f"{source_code}:")})


def normalized_core(normalized: NormalizedNotice) -> dict[str, Any]:
    core: dict[str, Any] = {}
    for name in CORE_FIELDS:
        item = normalized.fields.get(name)
        if item is None or item.status != "OK" or item.value is None:
            core[name] = None
        elif name in {"budget", "budget_cap"} and item.extra.get("amount_yuan") is not None:
            core[name] = f"yuan:{item.extra['amount_yuan']}"
        elif name in _DATETIME_FIELDS:
            core[name] = canonical_datetime(item.value)
        else:
            core[name] = item.value
    core["attachments"] = sorted(item.get("url", "") for item in normalized.attachments)
    return core


def core_from_normalized_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    fields = payload.get("fields") or {}
    core: dict[str, Any] = {}
    for name in CORE_FIELDS:
        item = fields.get(name) or {}
        if item.get("status") != "OK" or item.get("value") is None:
            core[name] = None
        elif name in {"budget", "budget_cap"} and item.get("amount_yuan") is not None:
            core[name] = f"yuan:{item['amount_yuan']}"
        elif name in _DATETIME_FIELDS:
            core[name] = canonical_datetime(item["value"])
        else:
            core[name] = item["value"]
    core["attachments"] = sorted(item.get("url", "") for item in (payload.get("attachments") or []))
    return core


def version_hash(content_sha256: str, core: dict[str, Any]) -> str:
    payload = json.dumps(core, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(f"{content_sha256}|{payload}".encode()).hexdigest()


def extract_requirement_blocks(lines: Iterable[str]) -> dict[str, str]:
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        heading = next((item for item in _REQUIREMENT_HEADINGS if item in line), None)
        if heading and len(line) <= 40:
            current = heading
            blocks.setdefault(current, [])
        elif current and len(line) <= 40 and _SECTION_MARKER_RE.match(line):
            current = None
        elif current:
            blocks[current].append(line)
    return {key: "\n".join(value) for key, value in blocks.items() if value}


def _fingerprint(*values: Any) -> str:
    payload = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def build_event(event_type: str, *, notice_version: int, old: Any = None, new: Any = None,
                extra: dict | None = None) -> dict:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"未知事件类型：{event_type}")
    payload: dict[str, Any] = {}
    if old is not None or new is not None:
        payload.update(old=old, new=new)
    if extra:
        payload.update(extra)
    return {
        "event_type": event_type,
        "notice_version": notice_version,
        "payload": payload,
        "dedupe_key": f"{event_type}:v{notice_version}:{_fingerprint(event_type, old, new)}",
    }


def detect_events(old_core: dict[str, Any] | None, new_core: dict[str, Any], *,
                  old_requirements: dict[str, str] | None = None,
                  new_requirements: dict[str, str] | None = None,
                  content_changed: bool = False, notice_version: int = 1) -> list[dict]:
    if old_core is None:
        return [build_event("DISCOVERED", notice_version=notice_version,
                            new={"project_name": new_core.get("project_name"),
                                 "project_code": new_core.get("project_code")})]

    events: list[dict] = []
    if old_core.get("bid_deadline") != new_core.get("bid_deadline"):
        events.append(build_event("DEADLINE_CHANGED", notice_version=notice_version,
                                  old=old_core.get("bid_deadline"), new=new_core.get("bid_deadline")))
    if (old_core.get("budget"), old_core.get("budget_cap")) != (
            new_core.get("budget"), new_core.get("budget_cap")):
        events.append(build_event(
            "BUDGET_CHANGED", notice_version=notice_version,
            old={"budget": old_core.get("budget"), "budget_cap": old_core.get("budget_cap")},
            new={"budget": new_core.get("budget"), "budget_cap": new_core.get("budget_cap")},
        ))
    added = sorted(set(new_core.get("attachments") or []) - set(old_core.get("attachments") or []))
    if added:
        events.append(build_event("DOCUMENT_ADDED", notice_version=notice_version,
                                  new=added, extra={"added_count": len(added)}))
    if old_requirements is not None and new_requirements is not None and old_requirements != new_requirements:
        changed = sorted(key for key in set(old_requirements) | set(new_requirements)
                         if old_requirements.get(key) != new_requirements.get(key))
        events.append(build_event("REQUIREMENT_CHANGED", notice_version=notice_version,
                                  new=changed, extra={"sections": changed}))
    old_type, new_type = str(old_core.get("notice_type") or ""), str(new_core.get("notice_type") or "")
    if not any(word in old_type for word in _AWARD_KEYWORDS) and any(word in new_type for word in _AWARD_KEYWORDS):
        events.append(build_event("AWARD_PUBLISHED", notice_version=notice_version,
                                  old=old_type, new=new_type))
    if content_changed or (old_core != new_core and not events):
        events.append(build_event("NOTICE_UPDATED", notice_version=notice_version,
                                  old=old_core, new=new_core,
                                  extra={"content_changed": content_changed}))
    return events


__all__ = [
    "CORE_FIELDS", "EVENT_TYPES", "SOURCE_HEALTH_EVENT_TYPES", "build_event", "canonical_datetime",
    "canonical_key", "core_from_normalized_payload", "detect_events", "extract_requirement_blocks",
    "normalise_text", "normalized_core", "possible_match_keys", "version_hash",
]
