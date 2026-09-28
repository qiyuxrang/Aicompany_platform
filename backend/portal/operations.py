import hashlib
import json
import math
import platform
import re
import time
from collections.abc import Mapping
from datetime import timedelta
from importlib.metadata import PackageNotFoundError, version as package_version
from threading import BoundedSemaphore, Event, Lock, Thread
from urllib.error import HTTPError, URLError
from urllib.request import Request
from zoneinfo import ZoneInfo

import django
from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import connection, transaction
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncDate
from django.http import Http404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ParseError
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from .integration import open_fixed
from .models import (AuditEvent, ModelCallLog, ModelRoute, Module, ModuleCheck, OperationalIssue,
                     Role, User, validate_module_url)
from .ops_metrics import get_performance
from .security import audit, authorized_modules


SHANGHAI = ZoneInfo("Asia/Shanghai")
PAGE_SIZE = 20
CHECK_COOLDOWN_SECONDS = 60
PROBE_DEADLINE_SECONDS = 3
PROBE_CAPACITY = 2
VALID_DAYS = {1, 7, 30}
AUDIT_ACTIONS = {
    "bootstrap_admin", "business_read", "businessmapping_change", "businessmapping_create",
    "login", "logout", "module_change", "module_check", "module_launch", "operational_issue_update",
    "password_change", "password_reset", "role_change", "ticket_issue", "ticket_redeem", "user_change",
    "user_create",
}
CHECK_MESSAGES = {
    ModuleCheck.State.REACHABLE: "固定模块地址可访问。",
    ModuleCheck.State.UNAVAILABLE: "固定模块地址当前不可访问。",
    ModuleCheck.State.NOT_CONFIGURED: "模块尚未配置可检查地址。",
    ModuleCheck.State.DISABLED: "模块已停用，未执行网络检查。",
    ModuleCheck.State.ERROR: "模块配置或检查过程异常。",
}
_SECRET_KEY = r"(?:password|passwd|secret|token|ticket|authorization|cookie|session|api[-_]?key)"
_SECRET_ASSIGNMENT = re.compile(
    rf"(?ix)(?:[\"']?{_SECRET_KEY}[\"']?\s*[:=]\s*|--{_SECRET_KEY}(?:=|\s+))"
    r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;}\]]+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_AUTHORIZATION = re.compile(r"(?i)\b(?:proxy-)?authorization\b[\"']?\s*[:=]\s*.*")
_URI_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s]*@")
_URL_QUERY = re.compile(r"(?i)(https?://[^\s?]+)\?\S+")
_probe_slots = BoundedSemaphore(PROBE_CAPACITY)
_probe_lock = Lock()
_probe_inflight = set()


class OpsAdminPermission(BasePermission):
    message = {"detail": "仅平台管理员可访问运维工作台。", "code": "ops_forbidden"}

    def has_permission(self, request, view):
        user = request.user
        return bool(user.is_authenticated and user.is_platform_admin and not user.must_change_password)


class CheckCooldown(Exception):
    def __init__(self, retry_after):
        self.retry_after = retry_after


class ProbeBusy(Exception):
    pass


def _safe_text(value, limit=200):
    text = " ".join(str(value or "").split())
    text = _AUTHORIZATION.sub("Authorization: [已屏蔽]", text)
    text = _URI_USERINFO.sub(r"\1[已屏蔽]@", text)
    text = _URL_QUERY.sub(r"\1?[已屏蔽]", text)
    text = _BEARER.sub("Bearer [已屏蔽]", text)
    text = _SECRET_ASSIGNMENT.sub("[敏感字段已屏蔽]", text)
    return text[:limit]


def _iso(value):
    return timezone.localtime(value, SHANGHAI).isoformat() if value else None


def _validate_query(request, allowed):
    for key in request.query_params:
        if key not in allowed or len(request.query_params.getlist(key)) != 1:
            raise ParseError("筛选参数无效。")


def _parse_page(value):
    try:
        page = int(value or "1")
    except (TypeError, ValueError) as error:
        raise ParseError("页码必须为正整数。") from error
    if not 1 <= page <= (2 ** 63 - 1) // PAGE_SIZE or str(page) != str(value or "1"):
        raise ParseError("页码必须为正整数。")
    return page


def _date_window(value, default):
    try:
        days = int(value or default)
    except (TypeError, ValueError) as error:
        raise ParseError("days仅支持1、7或30。") from error
    if days not in VALID_DAYS or str(days) != str(value or default):
        raise ParseError("days仅支持1、7或30。")
    end = timezone.now()
    end_local = timezone.localtime(end, SHANGHAI)
    start = end_local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    return days, start, end


def _range_data(days, start, end):
    return {"days": days, "start": _iso(start), "end": _iso(end), "timezone": "Asia/Shanghai"}


def _page_data(items, total, page):
    return {"items": items, "total": total, "page": page, "page_size": PAGE_SIZE,
            "pages": (total + PAGE_SIZE - 1) // PAGE_SIZE}


def _role_data(role):
    return {"code": _safe_text(role.code, 50), "name": _safe_text(role.name, 80)}


def _safe_module_url(module):
    if not module.url:
        return ""
    try:
        validate_module_url(module.url)
    except DjangoValidationError:
        return ""
    return module.url


def _module_digest(module):
    value = json.dumps(
        {"code": module.code, "enabled": module.enabled, "status": module.status, "url": module.url},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(value.encode()).hexdigest()


def _check_data(module, check):
    stale = check.config_digest != _module_digest(module)
    return {
        "state": check.state,
        "checked_at": _iso(check.checked_at),
        "duration_ms": check.duration_ms,
        "message": "模块配置已变更，请重新检查。" if stale else _safe_text(check.message),
        "next_check_at": _iso(check.next_check_at),
        "stale": stale,
    }


def _module_data(module):
    check = getattr(module, "latest_check", None)
    return {
        "id": module.pk,
        "code": _safe_text(module.code, 50),
        "name": _safe_text(module.name, 80),
        "description": _safe_text(module.description, 300),
        "url": _safe_module_url(module),
        "status": module.status if module.enabled else "disabled",
        "enabled": module.enabled,
        "admin_url": f"/admin/portal/module/{module.pk}/change/",
        "check": _check_data(module, check) if check else None,
    }


def _my_module_data(module):
    return {"code": _safe_text(module.code, 50), "name": _safe_text(module.name, 80),
            "status": module.status if module.enabled else "disabled", "enabled": module.enabled}


def _note_data(notes):
    if not isinstance(notes, list):
        return []
    result = []
    for note in notes[-20:]:
        if not isinstance(note, dict):
            continue
        at, actor, text = note.get("at"), note.get("actor"), note.get("text")
        if isinstance(at, str) and isinstance(actor, str) and isinstance(text, str):
            result.append({"at": _safe_text(at, 50), "actor": _safe_text(actor, 150),
                           "text": _safe_text(text, 500)})
    return result


def _issue_data(issue):
    return {
        "id": issue.pk,
        "module_code": _safe_text(issue.module.code, 50),
        "module_name": _safe_text(issue.module.name, 80),
        "severity": issue.severity,
        "title": _safe_text(issue.title, 120),
        "status": issue.status,
        "health_state": issue.health_state,
        "first_seen": _iso(issue.first_seen),
        "last_seen": _iso(issue.last_seen),
        "occurrences": issue.occurrences,
        "evidence": _safe_text(issue.evidence),
        "checked_at": _iso(issue.checked_at),
        "notes": _note_data(issue.notes),
        "admin_url": f"/admin/portal/module/{issue.module_id}/change/",
    }


def _audit_data(event):
    changes = []
    if isinstance(event.changes, list):
        for field in event.changes:
            field = _safe_text(field, 80)
            if field and re.fullmatch(r"[\w.-]+", field):
                changes.append(field)
    return {
        "id": event.pk,
        "created_at": _iso(event.created_at),
        "actor": _safe_text(event.actor.username, 150) if event.actor else "系统",
        "action": _safe_text(event.action, 80),
        "target": _safe_text(event.target, 150),
        "result": _safe_text(event.result, 30),
        "changes": changes,
    }


def _user_data(user):
    roles = sorted(user.roles.all(), key=lambda role: role.code)
    return {
        "id": user.pk,
        "username": _safe_text(user.username, 150),
        "display_name": _safe_text(user.display_name, 80),
        "is_active": user.is_active,
        "must_change_password": user.must_change_password,
        "roles": [_role_data(role) for role in roles],
        "last_login": _iso(user.last_login),
        "admin_url": f"/admin/portal/user/{user.pk}/change/",
        "password_url": f"/admin/portal/user/{user.pk}/password/",
    }


def _usage(start, end, module_code=None):
    events = AuditEvent.objects.filter(created_at__gte=start, created_at__lte=end)
    login_filter = Q(action="login", result="success", actor__isnull=False)
    launch_filter = Q(action="module_launch", result="success")
    if module_code:
        launch_filter &= Q(target=module_code)
    totals = events.aggregate(
        login_users=Count("actor", filter=login_filter, distinct=True),
        login_count=Count("id", filter=login_filter),
        module_launches=Count("id", filter=launch_filter),
    )
    grouped = events.annotate(day=TruncDate("created_at", tzinfo=SHANGHAI)).values("day").annotate(
        login_users=Count("actor", filter=login_filter, distinct=True),
        login_count=Count("id", filter=login_filter),
        module_launches=Count("id", filter=launch_filter),
    )
    grouped_by_day = {row["day"]: row for row in grouped}
    start_day = timezone.localtime(start, SHANGHAI).date()
    end_day = timezone.localtime(end, SHANGHAI).date()
    trend = []
    day = start_day
    while day <= end_day:
        row = grouped_by_day.get(day, {})
        trend.append({"date": day.isoformat(), "login_users": row.get("login_users", 0),
                      "login_count": row.get("login_count", 0), "module_launches": row.get("module_launches", 0)})
        day += timedelta(days=1)

    launches = events.filter(action="module_launch", result="success")
    if module_code:
        launches = launches.filter(target=module_code)
    launch_counts = {row["target"]: row["launches"]
                     for row in launches.values("target").annotate(launches=Count("id"))}
    modules = Module.objects.all()
    if module_code:
        modules = modules.filter(code=module_code)
    ranking = [{"code": module.code, "name": _safe_text(module.name, 80),
                "launches": launch_counts.get(module.code, 0)} for module in modules]
    ranking.sort(key=lambda item: (-item["launches"], item["code"]))
    return totals, trend, ranking


def _employee_usage(start, end, module_code=None):
    events = AuditEvent.objects.filter(created_at__gte=start, created_at__lte=end)
    login_filter = Q(action="login", result="success", actor__isnull=False)
    launch_filter = Q(action="module_launch", result="success")
    if module_code:
        launch_filter &= Q(target=module_code)
    employee_rows = events.filter(actor__isnull=False).values("actor_id").annotate(
        login_count=Count("id", filter=login_filter),
        module_launches=Count("id", filter=launch_filter),
    ).filter(Q(login_count__gt=0) | Q(module_launches__gt=0))
    employee_counts = {row["actor_id"]: row for row in employee_rows}
    employees = [{"id": user.pk, "username": _safe_text(user.username, 150),
                  "display_name": _safe_text(user.display_name, 80),
                  "login_count": employee_counts[user.pk]["login_count"],
                  "module_launches": employee_counts[user.pk]["module_launches"]}
                 for user in User.objects.filter(pk__in=employee_counts).exclude(roles__code="platform_admin")]
    employees.sort(key=lambda item: (-(item["login_count"] + item["module_launches"]),
                                     item["username"], item["id"]))
    return employees[:20]


def _model_usage(start, end):
    calls = ModelCallLog.objects.filter(purpose="business", created_at__gte=start, created_at__lte=end)
    call_totals = calls.aggregate(
        calls=Count("id"),
        successes=Count("id", filter=Q(status="success")),
        failures=Count("id", filter=~Q(status__in=("success", "pending"))),
        prompt_tokens=Sum("prompt_tokens"),
        completion_tokens=Sum("completion_tokens"),
    )
    route_counts = {row["route_id"]: row for row in calls.values("route_id").annotate(
        calls=Count("id"),
        successes=Count("id", filter=Q(status="success")),
        failures=Count("id", filter=~Q(status__in=("success", "pending"))),
    )}
    routes = [{"code": route.code, "name": _safe_text(route.name, 100),
               "calls": route_counts.get(route.pk, {}).get("calls", 0),
               "successes": route_counts.get(route.pk, {}).get("successes", 0),
               "failures": route_counts.get(route.pk, {}).get("failures", 0)}
              for route in ModelRoute.objects.all()]
    routes.sort(key=lambda item: (-item["calls"], item["code"]))
    return {
        "enabled_routes": ModelRoute.objects.filter(enabled=True).count(),
        "calls": call_totals["calls"],
        "successes": call_totals["successes"],
        "failures": call_totals["failures"],
        "prompt_tokens": call_totals["prompt_tokens"] if call_totals["calls"] else 0,
        "completion_tokens": call_totals["completion_tokens"] if call_totals["calls"] else 0,
        "routes": routes[:20],
    }


def _database_health():
    checked_at = timezone.now()
    started = time.perf_counter()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return {"state": "healthy", "checked_at": _iso(checked_at),
                "database_ms": round((time.perf_counter() - started) * 1000, 2),
                "message": "数据库连接正常。"}
    except Exception:
        return {"state": "unavailable", "checked_at": _iso(checked_at),
                "database_ms": round((time.perf_counter() - started) * 1000, 2),
                "message": "数据库当前不可用。"}


def _backup_report():
    checked_at = timezone.now()
    report_path = settings.BASE_DIR / "docs" / "evidence" / "postgres-restore-final.json"
    if not report_path.is_file():
        return {"state": "not_configured", "message": "未接入历史恢复演练报告。",
                "checked_at": _iso(checked_at), "record": None}
    try:
        if report_path.stat().st_size > 1_000_000:
            raise ValueError
        data = json.loads(report_path.read_text(encoding="utf-8"))
        timestamp = parse_datetime(data.get("timestamp")) if isinstance(data, dict) else None
        tables = data.get("tables") if isinstance(data, dict) else None
        source = data.get("source") if isinstance(data, dict) else None
        all_tables_equal = data.get("all_tables_equal") if isinstance(data, dict) else None
        source_unchanged = data.get("source_unchanged") if isinstance(data, dict) else None
        if (timestamp is None or timezone.is_naive(timestamp) or not isinstance(tables, dict)
                or not isinstance(source, str) or not source
                or not all(isinstance(name, str) and isinstance(value, dict)
                           and isinstance(value.get("rows"), int) and value["rows"] >= 0
                           and isinstance(value.get("sha256"), str)
                           and re.fullmatch(r"[0-9a-f]{64}", value["sha256"])
                           for name, value in tables.items())
                or not isinstance(all_tables_equal, bool) or not isinstance(source_unchanged, bool)):
            raise ValueError
        try:
            current_tables = set(connection.introspection.table_names())
            current_database = connection.settings_dict.get("NAME")
            source_matches = isinstance(current_database, str) and current_database == source
        except Exception:
            current_tables = set()
            source_matches = False
        record = {"timestamp": _iso(timestamp), "table_count": len(tables),
                  "all_tables_equal": all_tables_equal, "source_unchanged": source_unchanged,
                  "scope_matches": source_matches and set(tables) == current_tables}
        return {"state": "historical_record", "message": "仅为历史脱敏恢复演练记录，不代表当前备份可恢复。",
                "checked_at": _iso(checked_at), "record": record}
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return {"state": "invalid", "message": "历史恢复演练报告缺失必要字段或格式无效。",
                "checked_at": _iso(checked_at), "record": None}


def _version_data():
    try:
        app_version = package_version("enterprise-portal")
    except PackageNotFoundError:
        app_version = "0.1.0"
    engine = {"postgresql": "PostgreSQL", "sqlite": "SQLite"}.get(connection.vendor, "未知数据库")
    return f"企业协同门户 {app_version} · Python {platform.python_version()} · Django {django.get_version()} · {engine}"


def _environment_data():
    engine = {"postgresql": "PostgreSQL", "sqlite": "SQLite"}.get(connection.vendor, "未知")
    return {"mode": "debug" if settings.DEBUG else "production", "https": settings.HTTPS,
            "database_engine": engine, "timezone": "Asia/Shanghai",
            "host_metrics": {"state": "not_collected", "message": "CPU、整机内存和磁盘未采集。"}}


def probe_module(target):
    started = time.perf_counter()
    try:
        validate_module_url(target)
        with open_fixed(Request(target, method="HEAD")) as response:
            state = ModuleCheck.State.REACHABLE if 200 <= response.status < 300 else ModuleCheck.State.UNAVAILABLE
    except HTTPError as error:
        state = ModuleCheck.State.REACHABLE if error.code in (401, 403, 405) else ModuleCheck.State.UNAVAILABLE
    except DjangoValidationError:
        state = ModuleCheck.State.ERROR
    except (URLError, TimeoutError, OSError, ValueError):
        state = ModuleCheck.State.UNAVAILABLE
    except Exception:
        state = ModuleCheck.State.ERROR
    duration_ms = max(0, int(round((time.perf_counter() - started) * 1000)))
    return state, duration_ms, CHECK_MESSAGES[state]


def _claim_probe(code):
    with _probe_lock:
        if code in _probe_inflight or not _probe_slots.acquire(blocking=False):
            raise ProbeBusy
        _probe_inflight.add(code)


def _release_probe(code):
    with _probe_lock:
        if code in _probe_inflight:
            _probe_inflight.remove(code)
            _probe_slots.release()


def _probe_worker(code, target, result, done):
    try:
        result.append(probe_module(target))
    except Exception:
        result.append((ModuleCheck.State.ERROR, 0, CHECK_MESSAGES[ModuleCheck.State.ERROR]))
    finally:
        _release_probe(code)
        done.set()


def _run_probe(code, target):
    result = []
    done = Event()
    thread = Thread(target=_probe_worker, args=(code, target, result, done), daemon=True)
    try:
        thread.start()
    except Exception:
        _release_probe(code)
        return ModuleCheck.State.ERROR, 0, CHECK_MESSAGES[ModuleCheck.State.ERROR]
    if not done.wait(PROBE_DEADLINE_SECONDS):
        return (ModuleCheck.State.UNAVAILABLE, int(PROBE_DEADLINE_SECONDS * 1000),
                CHECK_MESSAGES[ModuleCheck.State.UNAVAILABLE])
    return result[0] if result else (ModuleCheck.State.ERROR, 0, CHECK_MESSAGES[ModuleCheck.State.ERROR])


def _reserve_check(code):
    now = timezone.now()
    claimed = False
    try:
        with transaction.atomic():
            module = Module.objects.select_for_update().filter(code=code).first()
            if module is None:
                raise Http404
            current = ModuleCheck.objects.filter(module=module).first()
            if current and current.next_check_at > now:
                raise CheckCooldown(max(1, math.ceil((current.next_check_at - now).total_seconds())))
            if not module.enabled:
                state = ModuleCheck.State.DISABLED
            elif module.status == Module.Status.PENDING or not module.url:
                state = ModuleCheck.State.NOT_CONFIGURED
            else:
                state = ModuleCheck.State.ERROR
                _claim_probe(module.code)
                claimed = True
            digest = _module_digest(module)
            ModuleCheck.objects.update_or_create(module=module, defaults={
                "state": state,
                "checked_at": now,
                "next_check_at": now + timedelta(seconds=CHECK_COOLDOWN_SECONDS),
                "duration_ms": None,
                "message": CHECK_MESSAGES[state] if state != ModuleCheck.State.ERROR else "检查已受理，正在执行受控探测。",
                "config_digest": digest,
            })
    except Exception:
        if claimed:
            _release_probe(code)
        raise
    return module, digest, now, state


def _sync_issue(module, state, checked_at):
    issue = OperationalIssue.objects.select_for_update().filter(module=module).first()
    if state == ModuleCheck.State.REACHABLE:
        if issue is not None:
            issue.status = OperationalIssue.Status.RECOVERED
            issue.recovered_at = checked_at
            issue.checked_at = checked_at
            issue.evidence = "固定模块地址已通过真实受控检查恢复可达。"
            issue.save(update_fields=["status", "recovered_at", "checked_at", "evidence"])
        return issue
    if state not in (ModuleCheck.State.UNAVAILABLE, ModuleCheck.State.ERROR):
        return issue
    severity = OperationalIssue.Severity.CRITICAL if state == ModuleCheck.State.UNAVAILABLE else OperationalIssue.Severity.WARNING
    title = "模块不可达" if state == ModuleCheck.State.UNAVAILABLE else "模块检查异常"
    evidence = "固定模块地址在受控检查中不可达。" if state == ModuleCheck.State.UNAVAILABLE else "固定模块配置无法完成受控检查。"
    if issue is None:
        return OperationalIssue.objects.create(module=module, severity=severity, title=title,
            first_seen=checked_at, last_seen=checked_at, evidence=evidence, checked_at=checked_at)
    issue.severity = severity
    issue.title = title
    issue.last_seen = checked_at
    issue.checked_at = checked_at
    issue.occurrences += 1
    issue.evidence = evidence
    issue.recovered_at = None
    if issue.status in (OperationalIssue.Status.CLOSED, OperationalIssue.Status.RECOVERED):
        issue.status = OperationalIssue.Status.OPEN
    issue.save(update_fields=["severity", "title", "last_seen", "checked_at", "occurrences", "evidence",
                                     "recovered_at", "status"])
    return issue


def _complete_check(module_id, digest, lease_at, state, duration_ms):
    finished_at = timezone.now()
    with transaction.atomic():
        module = Module.objects.select_for_update().get(pk=module_id)
        completed = ModuleCheck.objects.filter(
            module=module, config_digest=digest, checked_at=lease_at
        ).update(state=state, checked_at=finished_at, duration_ms=duration_ms, message=CHECK_MESSAGES[state])
        current = completed == 1 and _module_digest(module) == digest
        issue = _sync_issue(module, state, finished_at) if current else None
    return current, issue


@api_view(["GET"])
@permission_classes([OpsAdminPermission])
def ops_overview(request):
    _validate_query(request, {"days"})
    days, start, end = _date_window(request.query_params.get("days"), 7)
    totals, trend, _ = _usage(start, end)
    modules = Module.objects.select_related("latest_check").order_by("id")
    current_issues = OperationalIssue.objects.filter(last_seen__lte=end)
    recent_issues = current_issues.select_related("module")[:5]
    recent_audit = AuditEvent.objects.filter(created_at__lte=end).select_related("actor")[:10]
    my_modules = authorized_modules(request.user).order_by("id")
    return Response({
        "updated_at": _iso(end),
        "range": _range_data(days, start, end),
        "health": _database_health(),
        "accounts": {"enabled": User.objects.filter(is_active=True).count(), "total": User.objects.count()},
        "usage": {"login_users": totals["login_users"], "login_count": totals["login_count"],
                  "module_launches": totals["module_launches"]},
        "performance": get_performance(),
        "issue_count": current_issues.filter(
            Q(recovered_at__isnull=True) | Q(recovered_at__lt=F("last_seen"))
        ).count(),
        "trend": trend,
        "modules": [_module_data(module) for module in modules],
        "recent_issues": [_issue_data(issue) for issue in recent_issues],
        "recent_audit": [_audit_data(event) for event in recent_audit],
        "backup": _backup_report(),
        "version": _version_data(),
        "my_modules": [_my_module_data(module) for module in my_modules],
    })


@api_view(["GET"])
@permission_classes([OpsAdminPermission])
def ops_users(request):
    _validate_query(request, {"q", "status", "role", "page"})
    query = request.query_params.get("q", "").strip()
    status = request.query_params.get("status", "all")
    role = request.query_params.get("role", "")
    page = _parse_page(request.query_params.get("page"))
    if len(query) > 100 or status not in {"all", "active", "inactive"}:
        raise ParseError("用户筛选值无效。")
    if role == "all":
        role = ""
    if role and not Role.objects.filter(code=role).exists():
        raise ParseError("角色筛选值无效。")
    users = User.objects.prefetch_related("roles").order_by("id")
    if query:
        users = users.filter(Q(username__icontains=query) | Q(display_name__icontains=query))
    if status != "all":
        users = users.filter(is_active=status == "active")
    if role:
        users = users.filter(roles__code=role).distinct()
    total = users.count()
    offset = (page - 1) * PAGE_SIZE
    data = _page_data([_user_data(user) for user in users[offset:offset + PAGE_SIZE]], total, page)
    data["roles"] = [_role_data(item) for item in Role.objects.order_by("code")]
    return Response(data)


@api_view(["GET"])
@permission_classes([OpsAdminPermission])
def ops_user_detail(request, user_id):
    _validate_query(request, set())
    user = User.objects.prefetch_related("roles").filter(pk=user_id).first()
    if user is None:
        raise Http404
    data = _user_data(user)
    events = AuditEvent.objects.filter(actor=user, created_at__lte=timezone.now()).select_related("actor")[:20]
    data["recent_audit"] = [_audit_data(event) for event in events]
    return Response(data)


@api_view(["GET"])
@permission_classes([OpsAdminPermission])
def ops_usage(request):
    _validate_query(request, {"days", "module"})
    days, start, end = _date_window(request.query_params.get("days"), 7)
    module_code = request.query_params.get("module", "")
    if module_code and not Module.objects.filter(code=module_code).exists():
        raise ParseError("模块筛选值无效。")
    totals, trend, ranking = _usage(start, end, module_code or None)
    return Response({
        "updated_at": _iso(end),
        "range": _range_data(days, start, end),
        "summary": {"enabled_accounts": User.objects.filter(is_active=True).count(),
                    "login_users": totals["login_users"], "login_count": totals["login_count"],
                    "module_launches": totals["module_launches"]},
        "trend": trend,
        "ranking": ranking,
        "employees": _employee_usage(start, end, module_code or None),
        "model_usage": _model_usage(start, end),
        "definitions": {"login_users": "期间成功登录事件的去重账号数。",
                        "login_count": "期间成功登录事件次数。",
                        "module_launches": "期间模块启动成功事件次数。",
                        "module_filter": "模块筛选仅作用于启动数，登录与模型统计始终为全平台口径。",
                        "model_usage": "期间全平台业务模型调用统计，不含连接测试；pending计入调用但不计失败。"},
    })


@api_view(["GET"])
@permission_classes([OpsAdminPermission])
def ops_modules(request):
    _validate_query(request, set())
    modules = Module.objects.select_related("latest_check").order_by("id")
    return Response({"updated_at": _iso(timezone.now()), "items": [_module_data(module) for module in modules]})


@api_view(["POST"])
@permission_classes([OpsAdminPermission])
def ops_module_check(request, code):
    _validate_query(request, set())
    if not isinstance(request.data, Mapping) or request.data:
        raise ParseError("检查请求体必须为空对象。")
    try:
        module, digest, lease_at, initial_state = _reserve_check(code)
    except CheckCooldown as error:
        return Response({"detail": "检查冷却中，请稍后重试。"}, status=429,
                        headers={"Retry-After": str(error.retry_after)})
    except ProbeBusy:
        audit(request.user, "module_check", code, result="busy")
        return Response({"detail": "模块检查繁忙，请稍后重试。", "code": "probe_busy"}, status=503)
    issue = None
    result = initial_state
    if initial_state == ModuleCheck.State.ERROR:
        state, duration_ms, _ = _run_probe(module.code, str(module.url))
        if state not in (ModuleCheck.State.REACHABLE, ModuleCheck.State.UNAVAILABLE, ModuleCheck.State.ERROR):
            state = ModuleCheck.State.ERROR
        try:
            duration_ms = max(0, min(int(duration_ms), 3_600_000))
        except (TypeError, ValueError):
            duration_ms = 0
        current, issue = _complete_check(module.pk, digest, lease_at, state, duration_ms)
        result = state if current else "stale"
    audit(request.user, "module_check", module.code, result=result)
    module = Module.objects.select_related("latest_check").get(pk=module.pk)
    if issue is None:
        issue = OperationalIssue.objects.select_related("module").filter(module=module).first()
    else:
        issue = OperationalIssue.objects.select_related("module").get(pk=issue.pk)
    return Response({"module": _module_data(module), "issue": _issue_data(issue) if issue else None})


@api_view(["GET"])
@permission_classes([OpsAdminPermission])
def ops_issues(request):
    _validate_query(request, {"severity", "status", "module", "days", "page"})
    severity = request.query_params.get("severity", "all")
    status = request.query_params.get("status", "all")
    module_code = request.query_params.get("module", "")
    page = _parse_page(request.query_params.get("page"))
    _, start, end = _date_window(request.query_params.get("days"), 30)
    if severity not in {"all", *OperationalIssue.Severity.values}:
        raise ParseError("严重程度筛选值无效。")
    if status not in {"all", *OperationalIssue.Status.values}:
        raise ParseError("问题状态筛选值无效。")
    if module_code and not Module.objects.filter(code=module_code).exists():
        raise ParseError("模块筛选值无效。")
    issues = OperationalIssue.objects.filter(last_seen__gte=start, last_seen__lte=end).select_related("module")
    if severity != "all":
        issues = issues.filter(severity=severity)
    if status != "all":
        issues = issues.filter(status=status)
    if module_code:
        issues = issues.filter(module__code=module_code)
    total = issues.count()
    offset = (page - 1) * PAGE_SIZE
    return Response(_page_data([_issue_data(issue) for issue in issues[offset:offset + PAGE_SIZE]], total, page))


@api_view(["GET", "POST"])
@permission_classes([OpsAdminPermission])
def ops_issue_detail(request, issue_id):
    _validate_query(request, set())
    issue = OperationalIssue.objects.select_related("module").filter(pk=issue_id).first()
    if issue is None:
        raise Http404
    if request.method == "GET":
        return Response(_issue_data(issue))
    if not isinstance(request.data, Mapping) or not set(request.data).issubset({"status", "note"}):
        raise ParseError("问题更新参数无效。")
    has_status = "status" in request.data
    has_note = "note" in request.data
    if not has_status and not has_note:
        raise ParseError("至少提交状态或备注。")
    status = request.data.get("status")
    if has_status and (not isinstance(status, str) or status not in {
            OperationalIssue.Status.OPEN, OperationalIssue.Status.INVESTIGATING, OperationalIssue.Status.CLOSED}):
        raise ParseError("人工状态仅支持open、investigating或closed。")
    note = request.data.get("note")
    if has_note and (not isinstance(note, str) or not note.strip() or len(note.strip()) > 500):
        raise ParseError("备注必须为1至500个字符。")
    changes = []
    with transaction.atomic():
        issue = OperationalIssue.objects.select_for_update().select_related("module").get(pk=issue_id)
        if has_status and status == issue.status and not has_note:
            raise ParseError("未提交实际变更。")
        update_fields = []
        if has_status:
            issue.status = status
            update_fields.append("status")
            if status == OperationalIssue.Status.CLOSED:
                issue.closed_at = timezone.now()
                update_fields.append("closed_at")
            changes.append("status")
        if has_note:
            notes = issue.notes if isinstance(issue.notes, list) else []
            notes = notes[-19:] + [{"at": _iso(timezone.now()), "actor": _safe_text(request.user.username, 150),
                                    "text": _safe_text(note.strip(), 500)}]
            issue.notes = notes
            update_fields.append("notes")
            changes.append("note")
        issue.save(update_fields=update_fields)
        audit(request.user, "operational_issue_update", issue.pk, changes=changes)
    return Response(_issue_data(issue))


@api_view(["GET"])
@permission_classes([OpsAdminPermission])
def ops_maintenance(request):
    _validate_query(request, {"days", "page", "action"})
    _, start, end = _date_window(request.query_params.get("days"), 7)
    page = _parse_page(request.query_params.get("page"))
    action = request.query_params.get("action", "")
    if action == "all":
        action = ""
    if action and action not in AUDIT_ACTIONS:
        raise ParseError("审计动作筛选值无效。")
    events = AuditEvent.objects.filter(created_at__gte=start, created_at__lte=end).select_related("actor")
    if action:
        events = events.filter(action=action)
    total = events.count()
    offset = (page - 1) * PAGE_SIZE
    audit_page = _page_data([_audit_data(event) for event in events[offset:offset + PAGE_SIZE]], total, page)
    return Response({"updated_at": _iso(end),
                    "environment": _environment_data(), "performance": get_performance(),
                    "backup": _backup_report(), "version": _version_data(),
                    "deployment_history": {"state": "not_configured", "message": "未接入可靠部署历史。"},
                    "audit": audit_page})
