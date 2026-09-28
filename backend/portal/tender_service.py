"""Tender snapshot, normalization, version, opportunity, and event ingestion."""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.db import transaction
from django.utils.dateparse import parse_datetime

from . import tender_dedupe as dedupe
from .security import audit
from .tender_models import (
    TenderFetchRun,
    TenderNotice,
    TenderNoticeVersion,
    TenderOpportunity,
    TenderOpportunityEvent,
    TenderSnapshot,
    TenderSource,
    TenderSourceHealthEvent,
)
from .tender_normalize import (
    PublicationEvidence,
    STATUS_OK,
    html_to_lines,
    normalize_notice,
    publication_evidence,
)
from .tender_sources.base import FetchResult
from .tender_window import classify_window_date


class TenderIngestRejected(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass
class IngestOutcome:
    source_code: str
    source_notice_id: str
    snapshot_id: int
    content_sha256: str
    version_hash: str
    version_number: int
    notice_created: bool
    version_created: bool
    opportunity_created: bool
    opportunity_id: int
    events: list[str] = field(default_factory=list)
    changed_fields: list[str] = field(default_factory=list)
    unknown_fields: list[str] = field(default_factory=list)
    attachments_seen: int = 0
    publish_date: str | None = None
    publish_precision: str = "unknown"
    publish_provenance: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _to_datetime(value: str | None) -> datetime | None:
    return parse_datetime(str(value)) if value else None


def _yuan_literal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    try:
        return str(int(value)) if value == value.to_integral_value() else format(value.normalize(), "f")
    except (InvalidOperation, ValueError):
        return str(value)


def _amount_from_core(value: str | None) -> Decimal | None:
    if not value or not str(value).startswith("yuan:"):
        return None
    try:
        return Decimal(str(value)[5:])
    except InvalidOperation:
        return None


def _iso_utc(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def opportunity_core(opportunity: TenderOpportunity) -> dict:
    return {
        "project_name": opportunity.project_name or None,
        "project_code": opportunity.project_code or None,
        "notice_type": opportunity.notice_type or None,
        "purchaser": opportunity.purchaser or None,
        "agency": opportunity.agency or None,
        "region": opportunity.region or None,
        "publish_at": (_iso_utc(opportunity.publish_at) if opportunity.publish_at
                       else opportunity.publish_date.isoformat() if opportunity.publish_date else None),
        "signup_time": opportunity.signup_time_text or None,
        "bid_deadline": _iso_utc(opportunity.bid_deadline),
        "bid_open_at": _iso_utc(opportunity.bid_open_at),
        "budget": (f"yuan:{_yuan_literal(opportunity.budget_amount_yuan)}"
                   if opportunity.budget_amount_yuan is not None else (opportunity.budget_raw or None)),
        "budget_cap": (f"yuan:{_yuan_literal(opportunity.budget_cap_yuan)}"
                       if opportunity.budget_cap_yuan is not None else None),
        "procurement_method": opportunity.procurement_method or None,
        "contact_person": opportunity.contact_person or None,
        "contact_phone": opportunity.contact_phone or None,
        "attachments": sorted(opportunity.attachment_urls or []),
    }


def _verify_official_reference(result: FetchResult, source: TenderSource) -> None:
    if result.source_code != source.code:
        raise TenderIngestRejected("source_mismatch", "抓取结果与来源不一致。")
    if not result.source_notice_id or len(result.source_notice_id) > 200:
        raise TenderIngestRejected("invalid_notice_id", "来源公告标识无效。")

    from .tender_sources import registered_adapters

    adapter_code = source.adapter_code or source.code
    adapter = registered_adapters().get(adapter_code)
    if adapter is None or adapter.code != source.code:
        raise TenderIngestRejected("source_not_registered", "来源未注册为冻结公开来源。")
    parts = urlsplit(result.original_url)
    origin = f"{parts.scheme}://{parts.netloc}"
    if (parts.scheme != "https" or origin not in adapter.allowed_origins or parts.username
            or parts.password or parts.fragment):
        raise TenderIngestRejected("unverified_official_url", "公告链接不属于来源官方 HTTPS 域名。")

    notice_id = result.source_notice_id
    query = parse_qs(parts.query)
    valid = {
        "ccgp_national": parts.path.endswith(f"/{notice_id}.htm"),
        "sx_jk_ecai": (parts.path == "/portal/detail" and query.get("chnlcode") == ["tender"]
                       and query.get("docid") == [notice_id]),
        "shxjkjt": parts.path == "/notice/bidding-detail" and query.get("id") == [notice_id],
        "csg_bidding": bool(re.fullmatch(rf"/(?:zbgg|fzbgg)/{re.escape(notice_id)}\.jhtml", parts.path)),
    }.get(source.code, False)
    if not valid:
        raise TenderIngestRejected("unverified_official_url", "官方链接与来源公告标识不匹配。")


def _store_snapshot(content: bytes) -> tuple[str, str]:
    digest = hashlib.sha256(content).hexdigest()
    root = Path(settings.TENDER_STORAGE_ROOT)
    if not root.is_absolute():
        raise TenderIngestRejected("invalid_storage_root", "Tender 私有存储根必须是绝对路径。")
    relative = Path("snapshots") / digest[:2] / f"{digest}.bin"
    base = root.resolve()
    target = (base / relative).resolve()
    if not target.is_relative_to(base):
        raise TenderIngestRejected("invalid_storage_path", "快照路径越界。")
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise TenderIngestRejected("snapshot_content_conflict", "内容寻址快照发生摘要冲突。")
        return relative.as_posix(), digest
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return relative.as_posix(), digest


def _merge_source_facts(normalized, result: FetchResult, source: TenderSource) -> PublicationEvidence:
    metadata = result.source_metadata or {}
    title = str(metadata.get("title_from_list") or "").strip()
    if title and source.code in {"ccgp_national", "sx_jk_ecai", "shxjkjt", "csg_bidding"}:
        item = normalized.fields["project_name"]
        item.value, item.raw, item.status, item.rule = title, title, STATUS_OK, "source_metadata:title_from_list"
    if source.code == "ccgp_national":
        region = str(metadata.get("region") or "").strip()
        item = normalized.fields["region"]
        if region and item.status != STATUS_OK:
            item.value, item.raw, item.status, item.rule = region, region, STATUS_OK, "source_metadata:region"
    channel = str(metadata.get("channel") or "").strip()
    item = normalized.fields["notice_type"]
    if channel and (item.status != STATUS_OK or source.code == "csg_bidding"):
        item.value, item.raw, item.status, item.rule = channel, channel, STATUS_OK, "source_metadata:channel"

    known_urls = {item.get("url") for item in normalized.attachments}
    for attachment in result.attachment_refs or []:
        if attachment.get("url") and attachment["url"] not in known_urls:
            normalized.attachments.append(dict(attachment))
            known_urls.add(attachment["url"])

    evidence = publication_evidence(normalized, source_metadata=metadata)
    publish_field = normalized.fields["publish_at"]
    if evidence.verified and publish_field.status != STATUS_OK:
        publish_field.value = (evidence.publish_at.isoformat() if evidence.publish_at
                               else evidence.publish_date.isoformat())
        publish_field.raw = evidence.raw
        publish_field.status = STATUS_OK
        publish_field.rule = evidence.provenance
        publish_field.extra = {"precision": evidence.precision,
                               "date": evidence.publish_date.isoformat()}
    return evidence


def _stable_content_digest(result: FetchResult, digest: str, source: TenderSource) -> str:
    candidate = str((result.source_metadata or {}).get("stable_content_sha256") or "").lower()
    return candidate if source.code == "shxjkjt" and re.fullmatch(r"[0-9a-f]{64}", candidate) else digest


@transaction.atomic
def ingest_fetch_result(result: FetchResult, *, source: TenderSource,
                        run: TenderFetchRun | None = None, actor=None) -> IngestOutcome:
    """Persist one fetched notice; initial coverage rejects unverified/out-of-window dates."""
    _verify_official_reference(result, source)
    computed_digest = hashlib.sha256(result.raw_bytes).hexdigest()
    if result.sha256 and result.sha256.lower() != computed_digest:
        raise TenderIngestRejected("snapshot_digest_mismatch", "抓取摘要与公告原文不一致。")

    normalized = normalize_notice(
        result.raw_bytes,
        source_code=source.code,
        original_url=result.original_url,
        fallback_title=str((result.source_metadata or {}).get("title_from_list") or ""),
    )
    publication = _merge_source_facts(normalized, result, source)
    if not source.initial_coverage_complete:
        if not publication.verified:
            raise TenderIngestRejected("publish_date_unverified", publication.reason)
        window_value = publication.publish_at or publication.publish_date
        position = classify_window_date(window_value, precision=publication.precision, source_code=source.code)
        if position != "inside":
            raise TenderIngestRejected("publish_date_outside_initial_window",
                                       f"可信发布时间位于首次窗口之外：{position}")

    fetched_at = _to_datetime(result.fetched_at) or datetime.now(timezone.utc)
    storage_path, digest = _store_snapshot(result.raw_bytes)
    snapshot_metadata = dict(result.source_metadata or {})
    snapshot_metadata["official_url_verified"] = True
    snapshot_metadata["publication"] = {
        "date": publication.publish_date.isoformat() if publication.publish_date else None,
        "precision": publication.precision,
        "provenance": publication.provenance,
    }
    snapshot = TenderSnapshot.objects.create(
        source=source, run=run, url=result.original_url, fetched_at=fetched_at,
        http_status=result.http_status, content_type=(result.content_type or "")[:160],
        storage_path=storage_path, content_sha256=digest, byte_size=result.byte_size,
        source_metadata=snapshot_metadata,
    )

    core = dedupe.normalized_core(normalized)
    requirements = dedupe.extract_requirement_blocks(html_to_lines(result.raw_bytes))
    canonical = dedupe.canonical_key(source.code, core.get("project_code"), core.get("project_name"),
                                     result.source_notice_id)
    notice, notice_created = TenderNotice.objects.get_or_create(
        source=source,
        source_notice_id=result.source_notice_id,
        defaults={
            "canonical_key": canonical,
            "title": (core.get("project_name") or result.source_notice_id)[:500],
            "notice_type": (core.get("notice_type") or "")[:80],
            "original_url": result.original_url,
            "publish_at": publication.publish_at,
            "publish_date": publication.publish_date,
            "publish_precision": publication.precision,
            "first_seen_at": fetched_at,
            "last_seen_at": fetched_at,
        },
    )
    notice = TenderNotice.objects.select_for_update().get(pk=notice.pk)
    previous = notice.versions.order_by("-version").first()
    stable_digest = _stable_content_digest(result, digest, source)
    content_hash = dedupe.version_hash(stable_digest, core)
    existing_version = notice.versions.filter(content_hash=content_hash).first()

    if existing_version is not None:
        if fetched_at > notice.last_seen_at:
            notice.last_seen_at = fetched_at
            notice.save(update_fields=["last_seen_at", "updated_at"])
        opportunity = (TenderOpportunity.objects.filter(primary_notice=notice).first()
                       or TenderOpportunity.objects.filter(opportunity_key=notice.canonical_key).first())
        if opportunity is None:
            raise TenderIngestRejected("incomplete_existing_ingest", "已有公告版本缺少对应商机。")
        audit(actor, "tender_notice_ingested",
              f"{source.code}:{result.source_notice_id}:v{existing_version.version}")
        return _outcome(result, snapshot, existing_version, opportunity, publication,
                        notice_created=False, version_created=False, opportunity_created=False,
                        normalized=normalized)

    old_payload_core = dedupe.core_from_normalized_payload(previous.normalized if previous else None)
    old_requirements = (previous.normalized or {}).get("requirements") if previous else None
    changed: list[str] = []
    if previous:
        changed = sorted(name for name in dedupe.CORE_FIELDS
                         if (old_payload_core or {}).get(name) != core.get(name))
        if sorted((old_payload_core or {}).get("attachments") or []) != sorted(core.get("attachments") or []):
            changed.append("attachments")

    payload = normalized.to_dict()
    payload["requirements"] = requirements
    version_number = previous.version + 1 if previous else 1
    version = TenderNoticeVersion.objects.create(
        notice=notice, version=version_number, content_hash=content_hash, normalized=payload,
        attachments=normalized.attachments, snapshot=snapshot, change_summary=changed,
        supersedes=previous,
    )
    notice.canonical_key = canonical
    notice.title = (core.get("project_name") or notice.title)[:500]
    notice.notice_type = (core.get("notice_type") or notice.notice_type)[:80]
    notice.original_url = result.original_url
    notice.publish_at = publication.publish_at or notice.publish_at
    notice.publish_date = publication.publish_date or notice.publish_date
    notice.publish_precision = publication.precision if publication.verified else notice.publish_precision
    notice.last_seen_at = max(notice.last_seen_at, fetched_at)
    notice.current_version = version_number
    notice.save()

    opportunity, opportunity_created = TenderOpportunity.objects.get_or_create(
        opportunity_key=canonical,
        defaults={"source": source, "primary_notice": notice,
                  "status": TenderOpportunity.Status.ACTIVE, "first_seen_at": fetched_at},
    )
    old_core = None if opportunity_created else opportunity_core(opportunity)
    _materialize(opportunity, core, normalized, notice, source, fetched_at, publication)
    opportunity.possible_match_keys = _register_possible_matches(opportunity)
    opportunity.current_version = max(opportunity.current_version, version.version)
    opportunity.save()
    new_core = opportunity_core(opportunity)

    previous_digest = None
    if previous and previous.snapshot:
        previous_digest = (previous.snapshot.source_metadata.get("stable_content_sha256")
                           or previous.snapshot.content_sha256)
    events = dedupe.detect_events(
        old_core, new_core,
        old_requirements=old_requirements if not opportunity_created else None,
        new_requirements=requirements,
        content_changed=bool(previous and previous_digest != stable_digest),
        notice_version=version.version,
    )
    recorded: list[str] = []
    notice_key = hashlib.sha256(result.source_notice_id.encode()).hexdigest()[:12]
    for item in events:
        payload = dict(item["payload"])
        payload["source_notice_id"] = result.source_notice_id
        _, created = TenderOpportunityEvent.objects.get_or_create(
            opportunity=opportunity,
            dedupe_key=f"{source.code}:{notice_key}:{item['dedupe_key']}",
            defaults={"event_type": item["event_type"], "occurred_at": fetched_at,
                      "notice_version": version, "payload": payload},
        )
        if created:
            recorded.append(item["event_type"])

    audit(actor, "tender_notice_ingested", f"{source.code}:{result.source_notice_id}:v{version.version}",
          changes=changed)
    return _outcome(result, snapshot, version, opportunity, publication,
                    notice_created=notice_created, version_created=True,
                    opportunity_created=opportunity_created, normalized=normalized,
                    events=recorded, changed=changed)


def _outcome(result, snapshot, version, opportunity, publication, *, notice_created,
             version_created, opportunity_created, normalized, events=None, changed=None):
    return IngestOutcome(
        source_code=result.source_code, source_notice_id=result.source_notice_id,
        snapshot_id=snapshot.pk, content_sha256=snapshot.content_sha256,
        version_hash=version.content_hash, version_number=version.version,
        notice_created=notice_created, version_created=version_created,
        opportunity_created=opportunity_created, opportunity_id=opportunity.pk,
        events=events or [], changed_fields=changed or [],
        unknown_fields=normalized.unknown_fields, attachments_seen=len(normalized.attachments),
        publish_date=publication.publish_date.isoformat() if publication.publish_date else None,
        publish_precision=publication.precision, publish_provenance=publication.provenance,
    )


_FIELD_STATE = {
    "project_name": lambda item: item.project_name, "project_code": lambda item: item.project_code,
    "notice_type": lambda item: item.notice_type, "purchaser": lambda item: item.purchaser,
    "agency": lambda item: item.agency, "region": lambda item: item.region,
    "publish_at": lambda item: item.publish_at or item.publish_date,
    "signup_time": lambda item: item.signup_time_text, "bid_deadline": lambda item: item.bid_deadline,
    "bid_open_at": lambda item: item.bid_open_at, "budget": lambda item: item.budget_amount_yuan,
    "budget_cap": lambda item: item.budget_cap_yuan,
    "procurement_method": lambda item: item.procurement_method,
    "contact_person": lambda item: item.contact_person, "contact_phone": lambda item: item.contact_phone,
}


def _keep_text(current: str, candidate: str | None, limit: int) -> str:
    value = (candidate or "").strip()
    return value[:limit] if value else (current or "")


def _keep_value(current, candidate):
    return candidate if candidate is not None else current


def _register_possible_matches(opportunity: TenderOpportunity) -> list[str]:
    code = dedupe.normalise_text(opportunity.project_code)
    if not code:
        return sorted(opportunity.possible_match_keys or [])
    others = (TenderOpportunity.objects.filter(opportunity_key__endswith=f":code:{code}")
              .exclude(pk=opportunity.pk).only("id", "opportunity_key", "possible_match_keys"))
    linked: set[str] = set()
    for other in others:
        linked.add(other.opportunity_key)
        merged = sorted(set(other.possible_match_keys or []) | {opportunity.opportunity_key})
        if merged != sorted(other.possible_match_keys or []):
            other.possible_match_keys = merged
            other.save(update_fields=["possible_match_keys", "updated_at"])
    return sorted(set(opportunity.possible_match_keys or []) | linked)


def _materialize(opportunity: TenderOpportunity, core: dict, normalized, notice, source,
                 fetched_at: datetime, publication: PublicationEvidence) -> None:
    opportunity.source = source
    if opportunity.primary_notice_id is None:
        opportunity.primary_notice = notice
    for attribute, limit in (("project_name", 500), ("project_code", 200), ("purchaser", 300),
                             ("agency", 300), ("region", 120), ("notice_type", 80),
                             ("procurement_method", 80), ("contact_person", 120),
                             ("contact_phone", 120)):
        setattr(opportunity, attribute,
                _keep_text(getattr(opportunity, attribute), core.get(attribute), limit))
    opportunity.budget_amount_yuan = _keep_value(
        opportunity.budget_amount_yuan, _amount_from_core(core.get("budget")))
    opportunity.budget_cap_yuan = _keep_value(
        opportunity.budget_cap_yuan, _amount_from_core(core.get("budget_cap")))
    budget = normalized.fields.get("budget")
    opportunity.budget_raw = _keep_text(opportunity.budget_raw, budget.raw if budget else "", 200)
    opportunity.publish_at = _keep_value(opportunity.publish_at, publication.publish_at)
    opportunity.publish_date = _keep_value(opportunity.publish_date, publication.publish_date)
    if publication.verified:
        opportunity.publish_precision = publication.precision
    opportunity.bid_deadline = _keep_value(opportunity.bid_deadline, _to_datetime(core.get("bid_deadline")))
    opportunity.bid_open_at = _keep_value(opportunity.bid_open_at, _to_datetime(core.get("bid_open_at")))
    opportunity.signup_time_text = _keep_text(opportunity.signup_time_text, core.get("signup_time"), 300)
    attachments = set(opportunity.attachment_urls or []) | set(core.get("attachments") or [])
    opportunity.attachment_urls = sorted(attachments)
    opportunity.attachment_count = len(attachments)
    opportunity.unknown_fields = sorted(name for name, getter in _FIELD_STATE.items() if not getter(opportunity))
    opportunity.first_seen_at = opportunity.first_seen_at or fetched_at
    if any(word in opportunity.notice_type for word in ("中标", "成交")):
        opportunity.status = TenderOpportunity.Status.AWARDED
    elif any(word in opportunity.notice_type for word in ("更正", "变更", "澄清", "补充", "延期")):
        opportunity.status = TenderOpportunity.Status.UPDATED


@transaction.atomic
def record_source_health(source: TenderSource, *, event_type: str, reason_code: str = "",
                         detail: str = "", payload: dict | None = None,
                         occurred_at: datetime | None = None, actor=None) -> TenderSourceHealthEvent:
    if event_type not in dedupe.SOURCE_HEALTH_EVENT_TYPES:
        raise ValueError(f"未知来源健康事件类型：{event_type}")
    moment = occurred_at or datetime.now(timezone.utc)
    blocking = event_type in {"PREFLIGHT_BLOCKED", "FETCH_FAILED", "RATE_LIMITED"}
    if event_type in {"PREFLIGHT_OK", "RECOVERED"}:
        source.health_state = TenderSource.Health.OK
        source.consecutive_failures = 0
        source.last_success_at = moment
    elif blocking:
        source.health_state = (TenderSource.Health.BLOCKED if event_type == "PREFLIGHT_BLOCKED"
                               else TenderSource.Health.ERROR)
        source.consecutive_failures += 1
        source.last_failure_at = moment
    elif event_type == "DEGRADED":
        source.health_state = TenderSource.Health.DEGRADED
    source.health_detail = detail[:500]
    source.save(update_fields=["health_state", "health_detail", "consecutive_failures",
                               "last_success_at", "last_failure_at", "updated_at"])
    event, _ = TenderSourceHealthEvent.objects.get_or_create(
        source=source, dedupe_key=f"{event_type}:{reason_code}:{moment.date().isoformat()}",
        defaults={"event_type": event_type, "occurred_at": moment,
                  "reason_code": reason_code[:80], "detail": detail[:500], "payload": payload or {}},
    )
    audit(actor, "tender_source_health", source.code,
          result="failed" if blocking else "success", changes=["health_state"])
    return event


__all__ = [
    "IngestOutcome", "TenderIngestRejected", "ingest_fetch_result", "opportunity_core",
    "record_source_health",
]
