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
from django.db.models import Q, Count, Max, Min, Case, When, Value, IntegerField, OuterRef, Subquery, Exists
from django.db.models.functions import Coalesce, NullIf
from django.urls import path
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.decorators import api_view
from rest_framework.exceptions import ParseError
from rest_framework.response import Response

from .product_service import product_user_allowed
from .security import audit
from .tender_manual_refresh import ActiveRefresh, enqueue, serialize, schedule_status
from .tender_classification import INDUSTRIES, NOTICE_CATEGORIES, PROVINCES
from .tender_models import (TenderConsumerHeartbeat, TenderFetchRun, TenderManualRefresh,
                            TenderNotice, TenderOpportunity, TenderOpportunityUserState, TenderSource)
from .tender_grouping import group_key, group_members


MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20
BEIJING = ZoneInfo("Asia/Shanghai")
SOURCE_CODES = ("ccgp_national", "sx_jk_ecai", "shxjkjt", "csg_bidding", "qinyuan", "zmzb", "chnenergy", "yuneng")
COVERAGE_NOTE = "展示已接通公开来源的公告，持续更新；尚未覆盖全国全部网站。"
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
YULIN_REGION_PATTERN = (r'^(榆林市|陕西(省)?[ /·、-]*'
                        r'(榆林(市|$|[ /·、-])|榆阳(区|$|[ /·、-])'
                        r'|神木(市|$|[ /·、-])|府谷(县|$|[ /·、-])))')


def _administrative_region_option(value):
    """Conservatively reject prose/addresses; never derive a new place name."""
    if not value or len(value) > 40 or value != value.strip():
        return False
    if re.search(r'采购|政策|项目|服务|公司|企业|政府|执行|支持|落实|所在|地址|范围|开发区|园区', value):
        return False
    remainder = value
    for province in sorted({name for pair in PROVINCES for name in pair}, key=len, reverse=True):
        if remainder.startswith(province):
            remainder = remainder[len(province):].lstrip(' /·、-')
            if not remainder:
                return True
            break
    # Administrative suffixes only; streets, building numbers and sentences
    # cannot become filter values. Unknown bare names remain in the records.
    return bool(re.fullmatch(r'(?:[\u4e00-\u9fff]{1,10}?(?:自治州|自治县|地区|市|县|区|旗|盟)[ /·、-]*){1,3}', remainder))


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
    if parts.scheme != "https" or (parts.fragment and notice.source.code != 'yuneng'):
        return None
    code = notice.source.code
    notice_id = notice.source_notice_id
    if code == "ccgp_national":
        match = re.fullmatch(r"/cggg/(?:zygg|dfgg)/[a-z]+/\d{6}/(t\d{8}_\d+)\.htm", parts.path)
        valid = parts.netloc == "www.ccgp.gov.cn" and not parts.query and match and match.group(1) == notice_id
    elif code == "sx_jk_ecai":
        query = parse_qs(parts.query, keep_blank_values=True)
        valid = (parts.netloc == "www.sxjkjcpt.com" and parts.path == "/portal/detail"
                 and set(query) in ({"docid", "chnlcode"}, {"docid", "chnlcode", "objtype"})
                 and query.get("objtype", ["2"]) == ["2"] and query.get("docid") == [notice_id]
                 and query.get("chnlcode") == ["tender"] and re.fullmatch(r"[a-fA-F0-9]{32}", notice_id))
    elif code == "shxjkjt":
        query = parse_qs(parts.query)
        valid = (parts.netloc == "www.shxjkjt.com" and parts.path == "/notice/bidding-detail"
                 and set(query) == {"id"} and query.get("id") == [notice_id]
                 and re.fullmatch(r"\d{1,12}", notice_id))
    elif code == "csg_bidding":
        match = re.fullmatch(r"/(?:zbgg|fzbgg)/(\d{1,16})\.jhtml", parts.path)
        valid = parts.netloc == "www.bidding.csg.cn" and not parts.query and match and match.group(1) == notice_id
    elif code == "qinyuan":
        match = re.fullmatch(r"/cms/default/webfile/(?:1ywgg|2ywgg)/\d{8}/(\d{1,30})\.html", parts.path)
        valid = parts.netloc == "qyzb.shccmg.com" and not parts.query and match and match.group(1) == notice_id
    elif code == 'zmzb':
        match = re.fullmatch(r'/cms/channel/ywgg1(?:gc|hw|fw)/(\d{1,12})\.htm', parts.path)
        valid = parts.netloc == 'www.zmzb.com' and not parts.query and match and match.group(1) == notice_id
    elif code == 'chnenergy':
        match = re.fullmatch(r'/bidweb/001/001002/00100200[123]/\d{8}/([a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12})\.html', parts.path)
        valid = parts.netloc == 'www.chnenergybidding.com.cn' and not parts.query and match and match.group(1) == notice_id
    elif code == 'yuneng':
        match = re.fullmatch(r'/home/NoticeShow\?id=(\d{1,12})&annoType=1', parts.fragment)
        valid = parts.netloc == 'dzsw.sxylny.com' and parts.path == '/' and not parts.query and match and match.group(1) == notice_id
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


def _participation(opportunity):
    if getattr(opportunity, '_has_result', False) or opportunity.status == 'AWARDED' or opportunity.notice_category == 'result':
        return 'awarded', '已公布结果'
    deadline = getattr(opportunity, '_effective_deadline', opportunity.bid_deadline)
    if opportunity.status == 'CLOSED' or (deadline and deadline <= timezone.now()):
        return 'expired', '已截止'
    return ('open', '未截止') if deadline else ('unknown', '截止时间待核实')


def _user_state(opportunity, user=None):
    names = ('is_read', 'is_favorite', 'is_irrelevant')
    if hasattr(opportunity, '_is_read'):
        return {name: getattr(opportunity, '_' + name) for name in names}
    state = TenderOpportunityUserState.objects.filter(user=user, project_group_key=group_key(opportunity)).first() if user else None
    return {name: bool(state and getattr(state, name)) for name in names}


def _summary(opportunity, user=None):
    participation, participation_label = _participation(opportunity)
    tier = (opportunity.classification_evidence or {}).get('relevance_tier', 'related')
    notice_count = getattr(opportunity, '_notice_count', None)
    if notice_count is None:
        notice_count = TenderNotice.objects.filter(canonical_key__in=group_members(opportunity).values('opportunity_key')).count()
    return {
        "id": opportunity.pk,
        "project_name": opportunity.project_name,
        "project_code": opportunity.project_code,
        "region": opportunity.region,
        "purchaser": opportunity.purchaser,
        "agency": opportunity.agency,
        "notice_type": opportunity.notice_type,
        "procurement_method": opportunity.procurement_method,
        "industry_code": opportunity.industry_code,
        "industry_label": INDUSTRIES.get(opportunity.industry_code, '行业待核实'),
        "digital_tags": opportunity.digital_tags,
        "classification_status": opportunity.classification_status,
        "notice_category": opportunity.notice_category,
        "relevance_tier": tier,
        "relevance_label": '核心相关' if tier == 'core' else '包含相关内容',
        "participation_status": participation,
        "participation_label": participation_label,
        "user_state": _user_state(opportunity, user),
        "notice_count": notice_count,
        "budget": {
            "amount_yuan": _decimal(opportunity.budget_amount_yuan),
            "cap_yuan": _decimal(opportunity.budget_cap_yuan),
            "raw": opportunity.budget_raw,
        },
        "publish_at": _iso(opportunity.publish_at),
        "publish_date": opportunity.publish_date.isoformat() if opportunity.publish_date else None,
        "publish_precision": opportunity.publish_precision,
        "bid_deadline": _iso(getattr(opportunity, '_effective_deadline', opportunity.bid_deadline)),
        "status": opportunity.status,
        "status_label": participation_label,
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


def _detail(opportunity, user=None):
    payload = _summary(opportunity, user)
    notices = TenderNotice.objects.filter(canonical_key__in=group_members(opportunity).values('opportunity_key')).select_related("source").order_by('-publish_date', '-publish_at', '-id')
    attachments = {}
    from .tender_sources import registered_adapters
    for notice in notices:
        adapter = registered_adapters().get(notice.source.adapter_code or notice.source.code)
        for version in notice.versions.order_by('-version'):
            for item in version.attachments or []:
                url = item.get('url', '')
                parts = urlsplit(url)
                if adapter and parts.scheme == 'https' and not parts.username and not parts.password and f'{parts.scheme}://{parts.netloc}' in adapter.allowed_origins:
                    attachments.setdefault(url, {"url": url, "name": item.get('name') or item.get('label') or '公告附件',
                                                 "text_status": item.get('text_status', 'available')})
    for member in group_members(opportunity):
        for evidence in (member.extraction_evidence or {}).get('evidence', []):
            if evidence.get('url') in attachments:
                attachments[evidence['url']]['text_status'] = evidence.get('status', 'unverified')
    extraction = opportunity.extraction_evidence or {}
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
        "attachments": list(attachments.values()),
        "signup_time_text": opportunity.signup_time_text,
        "bid_open_at": _iso(opportunity.bid_open_at),
        "classification_evidence": opportunity.classification_evidence,
        "procurement_scope": ((extraction.get('fields') or {}).get('procurement_scope') or {}).get('value') or '',
        "field_evidence": extraction.get('fields', {}),
        "extraction_warnings": extraction.get('warnings', []),
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


def _grouped_base(user):
    queryset = TenderOpportunity.objects.select_related("source", "primary_notice", "primary_notice__source").annotate(
        _group_key=Coalesce(NullIf('project_group_key', Value('')), 'opportunity_key'))
    members = TenderOpportunity.objects.filter(Q(project_group_key=OuterRef('_group_key')) |
                                               Q(project_group_key='', opportunity_key=OuterRef('_group_key')))
    states = TenderOpportunityUserState.objects.filter(user=user, project_group_key=OuterRef('_group_key'))
    return queryset.annotate(
        _is_read=Exists(states.filter(is_read=True)),
        _is_favorite=Exists(states.filter(is_favorite=True)),
        _is_irrelevant=Exists(states.filter(is_irrelevant=True)),
        _has_result=Exists(members.filter(Q(notice_category='result') | Q(status='AWARDED'))),
        _effective_deadline=Subquery(members.filter(bid_deadline__isnull=False).order_by('-publish_date', '-publish_at', '-id').values('bid_deadline')[:1]),
        _group_first_seen=Subquery(members.filter(first_seen_at__isnull=False).order_by('first_seen_at').values('first_seen_at')[:1]),
    )


def _filtered(request):
    queryset = _grouped_base(request.user)
    params = request.query_params
    state = (params.get('user_state') or '').strip()
    if state not in ('', 'unread', 'read', 'favorite', 'irrelevant'):
        raise TenderApiError('invalid_param', 'user_state 取值无效。')
    queryset = queryset.filter(_is_irrelevant=state == 'irrelevant')
    if state in ('read', 'unread'):
        queryset = queryset.filter(_is_read=state == 'read')
    elif state == 'favorite':
        queryset = queryset.filter(_is_favorite=True)
    participation = (params.get('participation') or '').strip()
    if participation not in ('', 'open', 'unknown', 'expired'):
        raise TenderApiError('invalid_param', 'participation 取值无效。')
    if participation == 'open':
        queryset = queryset.filter(_effective_deadline__gt=timezone.now(), _has_result=False).exclude(status='CLOSED')
    elif participation == 'unknown':
        queryset = queryset.filter(_effective_deadline__isnull=True, _has_result=False).exclude(status='CLOSED')
    elif participation == 'expired':
        queryset = queryset.filter(Q(_effective_deadline__lte=timezone.now()) | Q(_has_result=True) | Q(status='CLOSED'))
    classification = (params.get('classification_status') or 'matched').strip()
    if classification not in ('matched', 'review', 'excluded', 'all'):
        raise TenderApiError('invalid_param', 'classification_status 取值无效。')
    if classification != 'all':
        queryset = queryset.filter(classification_status=classification)
    category = (params.get('notice_category') or 'procurement').strip()
    if category not in (*NOTICE_CATEGORIES, 'all'):
        raise TenderApiError('invalid_param', 'notice_category 取值无效。')
    if category != 'all':
        queryset = queryset.filter(notice_category=category)
    industries = [value.strip() for value in (params.get('industry') or '').split(',') if value.strip()]
    if set(industries) - set(INDUSTRIES):
        raise TenderApiError('invalid_param', 'industry 取值无效。')
    if industries:
        queryset = queryset.filter(industry_code__in=industries)
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
    ordering = (params.get("ordering") or "regional_priority").strip()
    representatives = queryset.order_by().values('_group_key').annotate(representative=Min('pk')).values('representative')
    queryset = queryset.filter(pk__in=Subquery(representatives))
    if ordering == 'regional_priority':
        # Only the persisted notice region supplies evidence. A purchaser's name,
        # the source's headquarters or words in the title never establish location.
        priority = Case(
            When(region__regex=YULIN_REGION_PATTERN, then=Value(0)),
            When(region__startswith='陕西', then=Value(1)),
            When(Q(region__startswith='内蒙古') | Q(region__startswith='山西')
                 | Q(region__startswith='宁夏'), then=Value(2)),
            default=Value(3), output_field=IntegerField(),
        )
        relevance = Case(When(classification_evidence__relevance_tier='core', then=Value(0)), default=Value(1), output_field=IntegerField())
        availability = Case(When(Q(_has_result=True) | Q(status='CLOSED') | Q(_effective_deadline__lte=timezone.now()), then=Value(2)),
                            When(_effective_deadline__gt=timezone.now(), then=Value(0)), default=Value(1), output_field=IntegerField())
        return queryset.alias(_region_priority=priority, _relevance_priority=relevance, _availability_priority=availability).order_by('_region_priority', '_availability_priority', '_relevance_priority', '-publish_date', '-publish_at', '-id')
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
    payload["trigger"] = getattr(batch, "trigger", "manual")
    for result in payload.get("results", {}).values():
        result["detail"] = _run_detail(result.get("state"), result.get("error_code"), result.get("complete"))
    return payload


def _run_detail(state, error_code, complete=None):
    details = {
        "source_not_registered": "该来源尚未接通。",
        "preflight_failed": "来源访问检查未通过，稍后重试。",
        "list_failed": "公告列表暂时无法读取，稍后重试。",
        "detail_failed": "部分公告未能完成读取或核验，已保留成功获取的公告。",
        "ingest_failed": "部分公告处理失败，已获取的公告保留。",
        "lease_lost": "更新已中断，等待后台恢复。",
        "execution_failed": "本次更新失败，等待下次更新。",
        "ingestion_disabled": "自动采集尚未启用。",
        "recovery_required": "上次更新中断，等待后台恢复。",
        "source_disabled": "该来源尚未启用。",
        "coverage_partial": "已更新可获取的公告，当前范围尚未全部核验。",
        "source_blocked": "来源暂不可访问，已保留获取结果并停止本次采集。",
    }
    if error_code:
        return details.get(error_code, "本次更新未完成，请查看来源状态。")
    if state == "SUCCESS":
        return "本次范围更新完成。" if complete else "已更新可获取的公告，当前范围尚未全部核验。"
    return {"RUNNING": "正在获取公开公告。", "QUEUED": "等待更新。",
            "BLOCKED": "来源暂不可访问。", "FAILED": "本次更新失败。",
            "WAITING_RETRY": "已保留获取结果，等待后续更新。",
            "SKIPPED": "本次未执行更新。"}.get(state, "")


def _refresh_availability():
    enabled = bool(getattr(settings, "PORTAL_TENDER_INGESTION_ENABLED", False)
                   and getattr(settings, "PORTAL_TENDER_MANUAL_REFRESH_ENABLED", False))
    consumer_online = TenderConsumerHeartbeat.objects.filter(
        slot=1, updated_at__gt=timezone.now() - timedelta(seconds=15)).exists()
    enabled_sources = set(TenderSource.objects.filter(
        code__in=SOURCE_CODES, enabled=True).values_list("code", flat=True))
    sources_ready = bool(enabled_sources)
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
    now = timezone.now()
    today = datetime.combine(now.astimezone(BEIJING).date(), time.min, BEIJING)
    stats = queryset.aggregate(
        total=Count('pk'),
        today_new=Count('pk', filter=Q(_group_first_seen__gte=today, _group_first_seen__lt=today + timedelta(days=1))),
        closing_soon=Count('pk', filter=Q(_effective_deadline__gt=now, _effective_deadline__lte=now + timedelta(days=7), _has_result=False) & ~Q(status='CLOSED')),
    )
    latest_batch = TenderManualRefresh.objects.filter(started_at__isnull=False).order_by('-created_at').first()
    new_ids = set()
    if latest_batch:
        new_ids = set(queryset.filter(_group_first_seen__gte=latest_batch.started_at,
                                     _group_first_seen__lte=latest_batch.finished_at or now).values_list('pk', flat=True))
    stats['latest_batch_new'] = len(new_ids)
    total = stats['total']
    # Old deep links can outlive their result set after reclassification or a
    # filter change. Return a real first page, never a misleading empty later page.
    if page > 1 and (page - 1) * page_size >= total:
        page = 1
    sources = TenderSource.objects.all()
    if request.query_params.get('source'):
        sources = sources.filter(code=request.query_params['source'])
    last_updated = sources.aggregate(moment=Max('last_success_at'))['moment']
    start = (page - 1) * page_size
    page_items = list(queryset[start:start + page_size])
    keys = {group_key(item) for item in page_items}
    members = TenderOpportunity.objects.filter(Q(project_group_key__in=keys) | Q(project_group_key='', opportunity_key__in=keys))
    notice_groups = {canonical: key or canonical for canonical, key in members.values_list('opportunity_key', 'project_group_key')}
    notice_counts = dict.fromkeys(keys, 0)
    for count in TenderNotice.objects.filter(canonical_key__in=notice_groups).order_by().values('canonical_key').annotate(total=Count('pk')):
        notice_counts[notice_groups[count['canonical_key']]] += count['total']
    for item in page_items:
        item._notice_count = notice_counts[group_key(item)]
    items = [{**_summary(item, request.user), 'latest_batch_new': item.pk in new_ids} for item in page_items]
    audit(request.user, "tender_opportunity_list", f"list:r{request.tender_request_id}")
    return Response({"items": items, "total": total, "page": page, "page_size": page_size,
                     "has_more": start + len(items) < total, "stats": stats,
                     "last_updated_at": _iso(last_updated)})


@api_view(["GET"])
@tender_endpoint
def opportunity_detail(request, opportunity_id):
    opportunity = _opportunity(opportunity_id)
    opportunity = _grouped_base(request.user).get(pk=opportunity.pk)
    audit(request.user, "tender_opportunity_read", f"{opportunity.pk}:r{request.tender_request_id}")
    return Response({"opportunity": _detail(opportunity, request.user)})


@api_view(['POST'])
@tender_endpoint
def opportunity_state(request, opportunity_id):
    opportunity = _opportunity(opportunity_id)
    allowed = {'is_read', 'is_favorite', 'is_irrelevant'}
    if (not isinstance(request.data, Mapping) or not request.data or set(request.data) - allowed
            or any(type(value) is not bool for value in request.data.values())):
        raise TenderApiError('invalid_request', '仅接受已读、关注和不相关的布尔标记。')
    state, _ = TenderOpportunityUserState.objects.get_or_create(user=request.user, project_group_key=group_key(opportunity))
    for name, value in request.data.items():
        setattr(state, name, value)
    state.save(update_fields=[*request.data, 'updated_at'])
    audit(request.user, 'tender_opportunity_state', str(opportunity.pk), changes=sorted(request.data))
    return Response({'user_state': {name: getattr(state, name) for name in sorted(allowed)}})


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
    regions = [{"value": value, "label": label} for value, label in PROVINCES]
    provincial_values = {name for pair in PROVINCES for name in pair}
    regions.extend(item for item in distinct('region') if item['value'] not in provincial_values
                   and _administrative_region_option(item['value']))
    regions.sort(key=lambda item: (0 if re.match(YULIN_REGION_PATTERN, item['value']) else
                                  1 if item['value'].startswith('陕西') else 2))
    audit(request.user, "tender_filter_options", f"options:r{request.tender_request_id}")
    return Response({
        "industries": [{"value": code, "label": label} for code, label in INDUSTRIES.items()],
        "notice_categories": [{"value": 'all', "label": '全部公告'}] + [
            {"value": code, "label": label} for code, label in NOTICE_CATEGORIES.items()],
        "need": [{"value": code, "label": label} for code, (label, _) in NEEDS.items()],
        "region": regions,
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
                            "detail": _run_detail(latest.state, latest.error_code, (latest.stats or {}).get("complete")),
                            "stats": {key: value for key, value in (latest.stats or {}).items()
                                      if key in ("complete", "listed", "ingested", "new_notices", "new_versions", "skipped")
                                      and isinstance(value, (int, bool))},
                            "created_at": _iso(latest.created_at),
                            "started_at": _iso(latest.started_at),
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
                         "schedule": schedule_status(),
                         "last_attempt_at": _iso(TenderFetchRun.objects.aggregate(value=Max("started_at"))["value"]),
                         "last_success_at": _iso(TenderSource.objects.aggregate(value=Max("last_success_at"))["value"]),
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
    approved_sources = list(TenderSource.objects.filter(
        code__in=SOURCE_CODES, enabled=True).order_by("code").values_list("code", flat=True))
    try:
        batch = enqueue(request.user, approved_sources)
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
    path("opportunities/<int:opportunity_id>/state/", opportunity_state),
    path("options/", filter_options),
    path("sources/", source_health),
    path("refresh/", refresh),
    path("refresh/<uuid:batch_id>/", refresh_batch),
]
