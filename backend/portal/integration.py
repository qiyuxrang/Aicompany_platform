import hashlib
import hmac
import json
import re
import secrets
from threading import BoundedSemaphore
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import BusinessMapping, IntegrationTicket, User, validate_module_url
from .security import audit, can_use_business, current_ticket_authorized


summary_slots = BoundedSemaphore(2)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, newurl):
        return None


def open_fixed(request):
    return build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=3)


def reachable(url):
    try:
        validate_module_url(url)
        with open_fixed(Request(url, method="HEAD")) as response:
            return 200 <= response.status < 300
    except HTTPError as error:
        return error.code in (401, 403, 405)
    except (ValidationError, URLError, TimeoutError, OSError, ValueError):
        return False


def issue_ticket(user):
    user = User.objects.get(pk=user.pk)
    if not can_use_business(user):
        raise PermissionError("经营模块未授权或不可用。")
    mapping = BusinessMapping.objects.filter(user=user, enabled=True).first()
    if mapping is None:
        raise PermissionError("尚未配置经营用户映射。")
    token = secrets.token_urlsafe(32)
    IntegrationTicket.objects.create(digest=hashlib.sha256(token.encode()).hexdigest(), user=user,
        mapping=mapping, external_user_id=mapping.external_user_id, session_version=user.session_version,
        grant_version=user.grant_version,
        expires_at=timezone.now() + timedelta(seconds=settings.TICKET_TTL_SECONDS))
    audit(user, "ticket_issue", "business")
    return token


@csrf_exempt
@require_POST
def redeem(request):
    expected = settings.INTEGRATION_SECRET
    supplied = request.headers.get("Authorization", "")
    if len(expected) < 40 or not hmac.compare_digest(supplied.encode(), ("Bearer " + expected).encode()):
        audit(None, "ticket_redeem", "business", "service_denied")
        return JsonResponse({"detail": "服务端身份校验失败。"}, status=403)
    try:
        body = json.loads(request.body)
        token = body.get("ticket")
        if not isinstance(token, str) or len(token) > 128 or body.get("audience") != "business" or body.get("purpose") != "read_summary":
            raise ValueError
    except (ValueError, AttributeError, UnicodeDecodeError):
        return JsonResponse({"detail": "兑换请求无效。"}, status=400)
    digest = hashlib.sha256(token.encode()).hexdigest()
    with transaction.atomic():
        ticket = IntegrationTicket.objects.select_related("user", "mapping").filter(digest=digest).first()
        if (not ticket or ticket.consumed_at or ticket.audience != body["audience"]
                or ticket.purpose != body["purpose"] or not current_ticket_authorized(ticket)):
            audit(None, "ticket_redeem", "business", "denied")
            return JsonResponse({"detail": "票据无效、过期、已使用或授权已撤销。"}, status=403)
        consumed = IntegrationTicket.objects.filter(pk=ticket.pk, consumed_at__isnull=True, expires_at__gt=timezone.now()).update(consumed_at=timezone.now())
        if consumed != 1:
            return JsonResponse({"detail": "票据已使用。"}, status=403)
        audit(ticket.user, "ticket_redeem", "business")
    return JsonResponse({"external_user_id": ticket.external_user_id, "audience": ticket.audience, "purpose": ticket.purpose})


def validate_summary(data):
    if not isinstance(data, dict) or set(data) != {"projects", "summary", "source", "updated_at"}:
        raise ValueError("response schema")
    if not isinstance(data["source"], str) or not data["source"] or len(data["source"]) > 100:
        raise ValueError("source")
    updated_at = parse_datetime(data["updated_at"]) if isinstance(data["updated_at"], str) else None
    if updated_at is None or timezone.is_naive(updated_at):
        raise ValueError("updated_at")
    if not isinstance(data["projects"], list) or len(data["projects"]) > 1000:
        raise ValueError("projects")
    for project in data["projects"]:
        if (not isinstance(project, dict) or set(project) != {"id", "name"}
                or not isinstance(project["id"], (str, int)) or isinstance(project["id"], bool)
                or not isinstance(project["name"], str) or len(project["name"]) > 200):
            raise ValueError("project")
    if not isinstance(data["summary"], dict) or not set(data["summary"]).issubset({"project_count", "contract_amount", "received_amount", "receivable_amount"}):
        raise ValueError("summary")
    for key, value in data["summary"].items():
        if key == "project_count":
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError("project count")
        elif not isinstance(value, str) or not re.fullmatch(r"-?\d{1,20}(?:\.\d{1,2})?", value):
            raise ValueError("summary value")
    return data


@api_view(["GET"])
def summary(request):
    if not can_use_business(request.user):
        audit(request.user, "business_read", "business", "permission_denied")
        return Response({"detail": "无经营数据访问权限。"}, status=403)
    if not settings.BUSINESS_SUMMARY_URL or len(settings.INTEGRATION_SECRET) < 40:
        return Response({"detail": "可信身份与只读数据接入尚未启用；导航入口仍保留原系统登录。", "code": "integration_not_configured"}, status=503)
    try:
        validate_module_url(settings.BUSINESS_SUMMARY_URL)
        token = issue_ticket(request.user)
    except PermissionError as error:
        audit(request.user, "business_read", "business", "mapping_denied")
        return Response({"detail": str(error)}, status=403)
    except ValidationError:
        return Response({"detail": "经营服务端目标未获信任。"}, status=503)
    upstream = Request(settings.BUSINESS_SUMMARY_URL, method="POST",
        data=json.dumps({"ticket": token, "audience": "business", "purpose": "read_summary"}).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + settings.INTEGRATION_SECRET})
    if not summary_slots.acquire(blocking=False):
        audit(request.user, "business_read", "business", "busy")
        return Response({"detail": "经营查询繁忙，请稍后重试。"}, status=503)
    try:
        with open_fixed(upstream) as response:
            if response.status != 200:
                raise ValueError("upstream status")
            raw = response.read(262145)
            if len(raw) > 262144:
                raise ValueError("response too large")
            data = validate_summary(json.loads(raw))
        ticket = IntegrationTicket.objects.select_related("user", "mapping").get(digest=hashlib.sha256(token.encode()).hexdigest())
        if not ticket.consumed_at or not current_ticket_authorized(ticket):
            raise PermissionError("授权已失效或旧系统未兑换票据。")
    except PermissionError:
        audit(request.user, "business_read", "business", "revoked")
        return Response({"detail": "经营访问授权已失效。"}, status=403)
    except HTTPError as error:
        audit(request.user, "business_read", "business", "upstream_denied" if error.code == 403 else "upstream_error")
        return Response({"detail": "原系统拒绝访问或暂不可用。"}, status=403 if error.code == 403 else 503)
    except (URLError, OSError, ValueError, TypeError, UnicodeDecodeError):
        audit(request.user, "business_read", "business", "upstream_error")
        return Response({"detail": "原系统离线、超时或返回无效数据；未使用缓存或替代数据。"}, status=503)
    finally:
        summary_slots.release()
    audit(request.user, "business_read", "business")
    return Response(data)
