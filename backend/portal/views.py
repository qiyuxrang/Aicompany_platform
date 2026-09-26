from collections.abc import Mapping
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import F, Q
from django.http import FileResponse, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import LoginAttempt, Module, User
from .security import audit, authorized_modules


def _portal_modules(user):
    result = authorized_modules(user)
    if user.is_active and not user.must_change_password:
        from .hr_models import ProbationCase
        if ProbationCase.objects.filter(assigned_manager=user).exists():
            result = Module.objects.filter(Q(pk__in=result.values("pk")) | Q(code="hr", enabled=True)).distinct()
    return result


def user_data(user):
    return {"id": user.pk, "username": user.username, "display_name": user.display_name,
            "roles": list(user.roles.values("code", "name")), "must_change_password": user.must_change_password,
            "is_platform_admin": user.is_platform_admin}


def module_data(module):
    return {"code": module.code, "name": module.name, "description": module.description,
            "status": module.status if module.enabled else "disabled", "enabled": module.enabled}


@require_GET
def csrf(request):
    return JsonResponse({"csrfToken": get_token(request)})


def csrf_failure(request, reason=""):
    return JsonResponse({"detail": "安全校验失败，请刷新页面重试。", "code": "csrf_failed"}, status=403)


def reserve_login(request, username):
    now = timezone.now()
    values = [("ip:" + request.META.get("REMOTE_ADDR", "unknown"), settings.LOGIN_IP_LIMIT),
              ("name:" + username.casefold(), settings.LOGIN_ATTEMPT_LIMIT)]
    keys = sorted((salted_hmac("portal.login", value).hexdigest(), limit) for value, limit in values)
    with transaction.atomic():
        buckets = []
        for key, limit in keys:
            LoginAttempt.objects.get_or_create(key=key, defaults={"started_at": now})
            bucket = LoginAttempt.objects.select_for_update().get(key=key)
            if bucket.started_at <= now - timedelta(seconds=settings.LOGIN_WINDOW_SECONDS):
                bucket.started_at, bucket.count = now, 0
            if bucket.count >= limit:
                return False, keys
            buckets.append(bucket)
        for bucket in buckets:
            bucket.count += 1
            bucket.save()
    return True, keys


@sensitive_post_parameters("password")
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@csrf_protect
def sign_in(request):
    if not isinstance(request.data, Mapping):
        return Response({"detail": "请求必须是对象。"}, status=400)
    username, password = request.data.get("username"), request.data.get("password")
    if not isinstance(username, str) or not isinstance(password, str) or len(username) > 150 or len(password) > 1024:
        return Response({"detail": "请输入有效的账号和密码。"}, status=400)
    allowed, keys = reserve_login(request, username)
    if not allowed:
        audit(None, "login", "buckets:" + ",".join(key for key, limit in keys), result="throttled")
        return Response({"detail": "尝试次数过多，请15分钟后再试。"}, status=429, headers={"Retry-After": "900"})
    user = authenticate(request, username=username, password=password)
    if user is None:
        audit(None, "login", "buckets:" + ",".join(key for key, limit in keys), result="failure")
        return Response({"detail": "账号或密码错误，或账号已停用。"}, status=401)
    LoginAttempt.objects.filter(key__in=[key for key, limit in keys], count__gt=0).update(count=F("count") - 1)
    login(request, user)
    request.session["version"] = user.session_version
    audit(user, "login", user.pk)
    return Response(user_data(user))


@api_view(["GET"])
def me(request):
    return Response(user_data(request.user))


@api_view(["POST"])
def sign_out(request):
    audit(request.user, "logout", request.user.pk)
    logout(request)
    return Response(status=204)


@sensitive_post_parameters("old_password", "new_password", "confirm_password")
@api_view(["POST"])
def change_password(request):
    if not isinstance(request.data, Mapping):
        return Response({"detail": "请求必须是对象。"}, status=400)
    old, new = request.data.get("old_password"), request.data.get("new_password")
    confirmation = request.data.get("confirm_password")
    if (not isinstance(new, str) or not new or len(new) > 1024
            or old is not None and (not isinstance(old, str) or len(old) > 1024)):
        return Response({"detail": "密码输入无效。"}, status=400)
    if "confirm_password" in request.data and (not isinstance(confirmation, str) or confirmation != new):
        return Response({"detail": "两次输入的新密码不一致。"}, status=400)
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=request.user.pk)
        # The authenticated first-login session proves the initial password already.
        # Keep the old-password API compatible; only the forced two-field form may omit it.
        if old is None:
            if not user.must_change_password:
                return Response({"detail": "修改密码需要验证当前密码。"}, status=400)
            if confirmation != new:
                return Response({"detail": "请再次输入新密码进行确认。"}, status=400)
        elif not user.check_password(old):
            return Response({"detail": "原密码不正确。"}, status=400)
        if user.check_password(new):
            return Response({"detail": "新密码不能与原密码相同。"}, status=400)
        try:
            validate_password(new, user)
        except ValidationError as error:
            return Response({"detail": " ".join(error.messages)}, status=400)
        user.set_password(new)
        user.must_change_password = False
        user.session_version += 1
        user.save(update_fields=["password", "must_change_password", "session_version"])
        audit(user, "password_change", user.pk)
    logout(request)
    return Response({"detail": "密码已修改，所有旧会话已失效，请重新登录。"})


@api_view(["GET"])
def modules(request):
    return Response([module_data(module) for module in _portal_modules(request.user).order_by("id")])


@api_view(["GET"])
def module_detail(request, code):
    module = get_object_or_404(_portal_modules(request.user), code=code)
    if not module.enabled:
        return Response({"detail": "模块已停用。"}, status=403)
    return Response(module_data(module))


@api_view(["POST"])
def launch(request, code):
    from .integration import reachable
    module = get_object_or_404(authorized_modules(request.user), code=code)
    if not module.enabled:
        return Response({"detail": "模块已停用。"}, status=403)
    if module.status == Module.Status.PENDING or not module.url:
        return Response({"detail": "此模块待接入，暂无可访问地址。"}, status=409)
    if not reachable(module.url):
        audit(request.user, "module_launch", code, "unavailable")
        return Response({"detail": "原系统暂不可访问，请联系模块负责人；平台不会自动启动或修改原系统。"}, status=503)
    audit(request.user, "module_launch", code)
    return Response({"url": module.url})


@require_GET
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


@require_GET
def frontend(request, path=""):
    if path in {"ops", "preview"} or path.startswith(("ops/", "preview/")):
        if not request.user.is_authenticated:
            return redirect("/login")
        if request.user.must_change_password:
            return redirect("/password")
        if not request.user.is_platform_admin:
            return JsonResponse({"detail": "仅平台管理员可访问此管理页面。"}, status=403)
    if path.startswith(("modules/", "centers/")):
        if not request.user.is_authenticated:
            return redirect("/login")
        if request.user.must_change_password:
            return redirect("/password")
        code = path.split("/")[1]
        modules = _portal_modules(request.user).filter(enabled=True)
        if (code == "hr" and modules.filter(code="hr").exists()
                and not authorized_modules(request.user).filter(code="hr", enabled=True).exists()):
            if path.rstrip("/") not in {"centers/hr", "centers/hr/probation"}:
                return JsonResponse({"detail": "仅可访问已分配的转正审批。"}, status=403)
        get_object_or_404(modules, code=code)
    root = settings.PORTAL_FRONTEND_DIST
    target = (root / path).resolve() if path.startswith("assets/") else root / "index.html"
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        return JsonResponse({"detail": "前端尚未构建，请运行 pnpm build。"}, status=404)
    return FileResponse(target.open("rb"))
