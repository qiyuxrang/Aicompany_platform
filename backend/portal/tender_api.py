from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Q
from django.urls import path
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.decorators import api_view
from rest_framework.exceptions import ParseError
from rest_framework.response import Response

from .product_service import product_user_allowed
from .security import audit
from .tender_manual_refresh import ActiveRefresh, enqueue, serialize
from .tender_models import (TenderConsumerHeartbeat, TenderManualRefresh,
                            TenderNotice, TenderOpportunity, TenderSource)


MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20
BEIJING = ZoneInfo("Asia/Shanghai")
SOURCE_CODES = ("ccgp_national", "sx_jk_ecai", "shxjkjt", "csg_bidding")
COVERAGE_NOTE = "仅展示实际已入库公告，并非全部站点全量覆盖。"
NEEDS = {
    "construction": ("工程施工", ("施工", "改造", "建设工程")),
    "equipment": ("设备材料", ("设备", "材料", "器材")),
    "information": ("信息化", ("信息化", "系统", "软件")),
    "consulting": ("设计咨询", ("设计", "咨询", "勘察")),
    "operations": ("运维服务", ("运维", "维护", "运营服务")),
}
PERIODS = {"today": "今天", "week": "近7天", "month": "近30天"}
DEADLINES = {"open": "未截止", "week": "未来7天", "month": "未来30天"}
BUDGETS = {
    "lt1m": "100万元以下",
    "1m_10m": "100万—1000万元",
    "gte10m": "1000万元及以上",
    "unknown": "预算未公布",
}
ORDERING_FIELDS = {
    "publish_at": "publish_at",
    "-publish_at": "-publish_at",
    "deadline": "bid_deadline",
    "-deadline": "-bid_deadline",
    "updated_at": "-updated_at",
    "budget": "budget_amount_yuan",
    "-budget": "-budget_amount_yuan",
}


class TenderApiError(Exception):
    def __init__(self, code, detail, status=400):
        self.code = code
        self.detail = detail
        self.status = status
        super().__init__(f"{code}: {detail}")


def _request_id(request):
    value = request.headers.get("X-Request-ID", "")
    return value if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", value) else uuid.uuid4().hex


def _error(request, error):
    return Response({"detail": error.detail, "code": error.code,
                     "request_id": request.tender_request_id}, status=error.status)


def tender_endpoint(function):
    @wraps(function)
    def wrapped(request, *args, **kwargs):
        request.tender_request_id = _request_id(request)
        try:
            if not product_user_allowed(getattr(request, "user", None)):
                raise TenderApiError("not_found", "对象不存在。", 404)
            response = function(request, *args, **kwargs)
        except TenderApiError as error:
            response = _error(request, error)
        except ParseError:
            response = _error(request, TenderApiError("invalid_request", "请求格式无效。"))
        response["X-Request-ID"] = request.tender_request_id
        response["Cache-Control"] = "private, no-store"
        return response

    return wrapped


def _iso(value):
    return value.isoformat() if value else None


def _decimal(value):
    if value is None:
        return None
    try:
        return format(value, "f")
    except (InvalidOperation, ValueError):
        return str(value)


def _official_notice_url(notice):
    if notice is None or notice.source_id is None:
        return None
    value = notice.original_url
    if (not value or "\\" in value or "%" in value
            or any(ord(character) < 33 or ord(character) > 126 for character in value)):
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme != "https" or parts.fragment:
        return None
    code = notice.source.code
    notice_id = notice.source_notice_id
    if code == "ccgp_national":
        match = re.fullmatch(r"/cggg/(?:zygg|dfgg)/\d{6}/(t\d{8}_\d+)\.htm", parts.path)
        valid = parts.netloc == "www.ccgp.gov.cn" and not parts.query and match and match.group(1) == notice_id
    elif code == "sx_jk_ecai":
        query = parse_qs(parts.query)
        valid = (parts.netloc == "www.sxjkjcpt.com" and parts.path == "/portal/detail"
                 and set(query) == {"docid", "chnlcode"} and query.get("docid") == [notice_id]
                 and query.get("chnlcode") == ["tender"] and re.fullmatch(r"[a-fA-F0-9]{32}", notice_id))
    elif code == "shxjkjt":
        query = parse_qs(parts.query)
        valid = (parts.netloc == "www.shxjkjt.com" and parts.path == "/notice/bidding-detail"
                 and set(query) == {"id"} and query.get("id") == [notice_id]
                 and re.fullmatch(r"\d{1,12}", notice_id))
    elif code == "csg_bidding":
        match = re.fullmatch(r"/(?:zbgg|fzbgg)/(\d{1,16})\.jhtml", parts.path)
        valid = parts.netloc == "www.bidding.csg.cn" and not parts.query and match and match.group(1) == notice_id
    else:
        valid = False
    return value if valid else None


def _source_payload(source):
    if source is None:
        return None
    return {
        "code": source.code,
        "name": source.name,
        "health_state": source.health_state,
        "health_label": source.get_health_state_display(),
        "health_detail": source.health_detail,
        "last_success_at": _iso(source.last_success_at),
        "last_failure_at": _iso(source.last_failure_at),
        "consecutive_failures": source.consecutive_failures,
    }


def _summary(opportunity):
    return {
        "id": opportunity.pk,
        "project_name": opportunity.project_name,
        "project_code": opportunity.project_code,
        "region": opportunity.region,
        "purchaser": opportunity.purchaser,
        "agency": opportunity.agency,
        "notice_type": opportunity.notice_type,
        "procurement_method": opportunity.procurement_method,
        "budget": {
            "amount_yuan": _decimal(opportunity.budget_amount_yuan),
            "cap_yuan": _decimal(opportunity.budget_cap_yuan),
            "raw": opportunity.budget_raw,
        },
        "publish_at": _iso(opportunity.publish_at),
        "publish_date": opportunity.publish_date.isoformat() if opportunity.publish_date else None,
        "publish_precision": opportunity.publish_precision,
        "bid_deadline": _iso(opportunity.bid_deadline),
        "status": opportunity.status,
        "status_label": opportunity.get_status_display(),
        "source": _source_payload(opportunity.source),
        "original_url": _official_notice_url(opportunity.primary_notice),
        "current_version": opportunity.current_version,
        "first_seen_at": _iso(opportunity.first_seen_at),
        "updated_at": _iso(opportunity.updated_at),
    }


def _versions(notice):
    if notice is None:
        return []
    return [{
        "version": item.version,
        "content_hash": item.content_hash,
        "change_summary": item.change_summary,
        "is_current": item.version == notice.current_version,
        "snapshot_id": item.snapshot_id,
        "snapshot_sha256": item.snapshot.content_sha256 if item.snapshot else None,
        "fetched_at": _iso(item.snapshot.fetched_at) if item.snapshot else None,
        "created_at": _iso(item.created_at),
    } for item in notice.versions.select_related("snapshot").order_by("-version")]


def _detail(opportunity):
    payload = _summary(opportunity)
    notices = TenderNotice.objects.filter(canonical_key=opportunity.opportunity_key).select_related("source")
    payload.update({
        "notices": [{
            "id": notice.pk,
            "title": notice.title,
            "notice_type": notice.notice_type,
            "source": _source_payload(notice.source),
            "original_url": url,
            "publish_at": _iso(notice.publish_at),
            "publish_date": notice.publish_date.isoformat() if notice.publish_date else None,
            "publish_precision": notice.publish_precision,
        } for notice in notices if (url := _official_notice_url(notice))],
        "versions": _versions(opportunity.primary_notice),
        "contact": {"person": opportunity.contact_person, "phone": opportunity.contact_phone},
        "attachment_count": opportunity.attachment_count,
        "unknown_fields": opportunity.unknown_fields,
        "possible_match_keys": opportunity.possible_match_keys,
    })
    return payload


def _int_param(params, name, default, minimum, maximum=None):
    raw = params.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise TenderApiError("invalid_param", f"{name} 必须是整数。") from None
    if value < minimum or (maximum is not None and value > maximum):
        raise TenderApiError("invalid_param", f"{name} 超出允许范围。")
    return value


def _decimal_param(params, name):
    raw = params.get(name)
    if raw in (None, ""):
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        raise TenderApiError("invalid_param", f"{name} 必须是数字。") from None
    if not value.is_finite() or value < 0:
        raise TenderApiError("invalid_param", f"{name} 必须是非负有限数。")
    return value


def _datetime_param(params, name):
    raw = params.get(name)
    if raw in (None, ""):
        return None
    value = parse_datetime(str(raw))
    if value is None:
        raise TenderApiError("invalid_param", f"{name} 必须是 ISO 日期时间。")
    return value


def _filtered(request):
    queryset = TenderOpportunity.objects.select_related("source", "primary_notice", "primary_notice__source")
    params = request.query_params
    keyword = (params.get("q") or "").strip()
    if keyword:
        queryset = queryset.filter(Q(project_name__icontains=keyword)
                                   | Q(project_code__icontains=keyword)
                                   | Q(purchaser__icontains=keyword)
                                   | Q(agency__icontains=keyword))
    for parameter, field in (("region", "region"), ("purchaser", "purchaser"),
                             ("notice_type", "notice_type")):
        value = (params.get(parameter) or "").strip()
        if value:
            queryset = queryset.filter(**{f"{field}__icontains": value})
    status = (params.get("status") or "").strip()
    if status:
        if status not in TenderOpportunity.Status.values:
            raise TenderApiError("invalid_param", "status 取值无效。")
        queryset = queryset.filter(status=status)
    source = (params.get("source") or "").strip()
    if source:
        queryset = queryset.filter(source__code=source)
    need = (params.get("need") or "").strip()
    if need:
        if need not in NEEDS:
            raise TenderApiError("invalid_param", "need 取值无效。")
        queryset = queryset.filter(Q(*[Q(project_name__icontains=term) for term in NEEDS[need][1]],
                                      _connector=Q.OR))
    local_now = timezone.now().astimezone(BEIJING)
    today = datetime.combine(local_now.date(), time.min, BEIJING)
    publish_period = (params.get("publish_period") or "").strip()
    if publish_period:
        if publish_period not in PERIODS:
            raise TenderApiError("invalid_param", "publish_period 取值无效。")
        days = {"today": 0, "week": 6, "month": 29}[publish_period]
        queryset = queryset.filter(publish_at__gte=today - timedelta(days=days),
                                   publish_at__lt=today + timedelta(days=1))
    deadline_period = (params.get("deadline_period") or "").strip()
    if deadline_period:
        if deadline_period not in DEADLINES:
            raise TenderApiError("invalid_param", "deadline_period 取值无效。")
        queryset = queryset.filter(bid_deadline__gt=local_now)
        if deadline_period != "open":
            queryset = queryset.filter(bid_deadline__lt=local_now + timedelta(
                days=7 if deadline_period == "week" else 30))
    budget_band = (params.get("budget_band") or "").strip()
    if budget_band:
        if budget_band not in BUDGETS:
            raise TenderApiError("invalid_param", "budget_band 取值无效。")
        if budget_band == "unknown":
            queryset = queryset.filter(budget_amount_yuan__isnull=True)
        elif budget_band == "lt1m":
            queryset = queryset.filter(budget_amount_yuan__lt=1000000)
        elif budget_band == "1m_10m":
            queryset = queryset.filter(budget_amount_yuan__gte=1000000,
                                       budget_amount_yuan__lt=10000000)
        else:
            queryset = queryset.filter(budget_amount_yuan__gte=10000000)
    for field, start_name, end_name in (("publish_at", "publish_from", "publish_to"),
                                        ("bid_deadline", "deadline_from", "deadline_to")):
        start, end = _datetime_param(params, start_name), _datetime_param(params, end_name)
        if start is not None:
            queryset = queryset.filter(**{f"{field}__gte": start})
        if end is not None:
            queryset = queryset.filter(**{f"{field}__lte": end})
    budget_min, budget_max = _decimal_param(params, "budget_min"), _decimal_param(params, "budget_max")
    if budget_min is not None:
        queryset = queryset.filter(budget_amount_yuan__gte=budget_min)
    if budget_max is not None:
        queryset = queryset.filter(budget_amount_yuan__lte=budget_max)
    ordering = (params.get("ordering") or "-publish_at").strip()
    if ordering not in ORDERING_FIELDS:
        raise TenderApiError("invalid_param", "ordering 取值无效。")
    return queryset.order_by(ORDERING_FIELDS[ordering], "-id")


def _opportunity(opportunity_id):
    try:
        return TenderOpportunity.objects.select_related(
            "source", "primary_notice", "primary_notice__source").get(pk=opportunity_id)
    except (TenderOpportunity.DoesNotExist, TypeError, ValueError):
        raise TenderApiError("not_found", "对象不存在。", 404) from None


def _batch_payload(batch):
    payload = serialize(batch)
    display_state = payload.get("display_state", payload.get("state", batch.state))
    payload.setdefault("stored_state", batch.state)
    payload.setdefault("display_state", display_state)
    payload.setdefault("state", display_state)
    return payload


def _refresh_availability():
    enabled = bool(getattr(settings, "PORTAL_TENDER_INGESTION_ENABLED", False)
                   and getattr(settings, "PORTAL_TENDER_MANUAL_REFRESH_ENABLED", False))
    consumer_online = TenderConsumerHeartbeat.objects.filter(
        slot=1, updated_at__gt=timezone.now() - timedelta(seconds=15)).exists()
    enabled_sources = set(TenderSource.objects.filter(
        code__in=SOURCE_CODES, enabled=True).values_list("code", flat=True))
    sources_ready = enabled_sources == set(SOURCE_CODES)
    return {
        "enabled": enabled,
        "consumer_online": consumer_online,
        "sources_ready": sources_ready,
        "available": enabled and consumer_online and sources_ready,
    }


@api_view(["GET"])
@tender_endpoint
def opportunity_list(request):
    queryset = _filtered(request)
    page = _int_param(request.query_params, "page", 1, 1)
    page_size = _int_param(request.query_params, "page_size", DEFAULT_PAGE_SIZE, 1, MAX_PAGE_SIZE)
    total = queryset.count()
    start = (page - 1) * page_size
    items = [_summary(item) for item in queryset[start:start + page_size]]
    audit(request.user, "tender_opportunity_list", f"list:r{request.tender_request_id}")
    return Response({"items": items, "total": total, "page": page, "page_size": page_size,
                     "has_more": start + len(items) < total})


@api_view(["GET"])
@tender_endpoint
def opportunity_detail(request, opportunity_id):
    opportunity = _opportunity(opportunity_id)
    audit(request.user, "tender_opportunity_read", f"{opportunity.pk}:r{request.tender_request_id}")
    return Response({"opportunity": _detail(opportunity)})


@api_view(["GET"])
@tender_endpoint
def opportunity_versions(request, opportunity_id):
    opportunity = _opportunity(opportunity_id)
    audit(request.user, "tender_opportunity_versions", f"{opportunity.pk}:r{request.tender_request_id}")
    return Response({"opportunity_id": opportunity.pk,
                     "versions": _versions(opportunity.primary_notice)})


@api_view(["GET"])
@tender_endpoint
def filter_options(request):
    base = TenderOpportunity.objects

    def distinct(field):
        return [{"value": value, "label": value} for value in
                base.exclude(**{field: ""}).values_list(field, flat=True).distinct().order_by(field)]

    source_codes = base.filter(source__isnull=False).values_list("source__code", flat=True).distinct()
    sources = TenderSource.objects.filter(code__in=source_codes).order_by("name")
    audit(request.user, "tender_filter_options", f"options:r{request.tender_request_id}")
    return Response({
        "need": [{"value": code, "label": label} for code, (label, _) in NEEDS.items()],
        "region": distinct("region"),
        "purchaser": distinct("purchaser"),
        "notice_type": distinct("notice_type"),
        "source": [{"value": source.code, "label": source.name} for source in sources],
        "status": [{"value": value, "label": label} for value, label in TenderOpportunity.Status.choices],
        "publish_period": [{"value": value, "label": label} for value, label in PERIODS.items()],
        "deadline_period": [{"value": value, "label": label} for value, label in DEADLINES.items()],
        "budget_band": [{"value": value, "label": label} for value, label in BUDGETS.items()],
        "coverage_note": COVERAGE_NOTE,
    })


@api_view(["GET"])
@tender_endpoint
def source_health(request):
    items = []
    for source in TenderSource.objects.filter(code__in=SOURCE_CODES).order_by("code"):
        latest = source.runs.order_by("-created_at", "-id").first()
        items.append({
            **_source_payload(source),
            "enabled": source.enabled,
            "latest_run": ({"id": latest.pk, "state": latest.state,
                            "error_code": latest.error_code,
                            "created_at": _iso(latest.created_at),
                            "finished_at": _iso(latest.finished_at)} if latest else None),
        })
    audit(request.user, "tender_source_health", f"sources:r{request.tender_request_id}")
    return Response({"items": items, "coverage_note": COVERAGE_NOTE})


@api_view(["GET", "POST"])
@tender_endpoint
def refresh(request):
    active = TenderManualRefresh.objects.filter(
        state__in=[TenderManualRefresh.State.QUEUED, TenderManualRefresh.State.RUNNING]
    ).order_by("created_at").first()
    if request.method == "GET":
        latest = active or TenderManualRefresh.objects.order_by("-created_at").first()
        audit(request.user, "tender_refresh_state", f"refresh:r{request.tender_request_id}")
        return Response({**_refresh_availability(),
                         "batch": _batch_payload(latest) if latest else None})
    if not isinstance(request.data, Mapping) or request.data:
        raise TenderApiError("invalid_request", "刷新请求不接受参数。")
    if active is not None:
        payload = _batch_payload(active)
        return Response({"code": "refresh_running", "detail": "已有刷新正在等待或运行。",
                         "batch_id": str(active.pk), "request_id": request.tender_request_id,
                         "batch": payload}, status=409)
    if not _refresh_availability()["available"]:
        raise TenderApiError("refresh_unavailable", "刷新未启用、消费者离线或来源未就绪。", 503)
    try:
        batch = enqueue(request.user, list(SOURCE_CODES))
    except ActiveRefresh as error:
        return Response({"code": "refresh_running", "detail": "已有刷新正在等待或运行。",
                         "batch_id": str(error.batch_id), "request_id": request.tender_request_id}, status=409)
    except ValueError as error:
        raise TenderApiError("refresh_unavailable", str(error), 503) from None
    audit(request.user, "tender_manual_refresh_queued", str(batch.pk), changes=["state"])
    return Response({"batch_id": str(batch.pk), **_batch_payload(batch)}, status=202)


@api_view(["GET"])
@tender_endpoint
def refresh_batch(request, batch_id):
    batch = TenderManualRefresh.objects.filter(pk=batch_id).first()
    if batch is None:
        raise TenderApiError("not_found", "对象不存在。", 404)
    audit(request.user, "tender_refresh_read", f"{batch.pk}:r{request.tender_request_id}")
    return Response(_batch_payload(batch))


urlpatterns = [
    path("opportunities/", opportunity_list),
    path("opportunities/<int:opportunity_id>/", opportunity_detail),
    path("opportunities/<int:opportunity_id>/versions/", opportunity_versions),
    path("options/", filter_options),
    path("sources/", source_health),
    path("refresh/", refresh),
    path("refresh/<uuid:batch_id>/", refresh_batch),
]
