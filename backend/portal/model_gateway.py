import json
from datetime import timedelta
from http.client import HTTPException
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .integration import NoRedirect
from .security import audit, authorized_modules


ERROR_MESSAGES = {
    "unconfigured": "模型网关尚未配置。",
    "unauthorized": "模型网关服务身份验证失败。",
    "invalid_request": "模型调用参数格式不正确。",
    "request_too_large": "模型请求内容过大。",
    "busy": "模型网关繁忙，请稍后重试。",
    "rate_limited": "模型服务限流，请稍后重试。",
    "upstream_auth": "模型服务密钥无效或无访问权限。",
    "timeout": "模型服务请求超时。",
    "upstream_error": "模型服务调用失败。",
    "invalid_response": "模型服务返回格式不符合协议。",
    "response_too_large": "模型服务返回内容过大。",
    "output_truncated": "模型输出被截断，请调整章节或令牌限制后由人工重试。",
    "target_not_allowed": "模型服务地址未获允许。",
    "missing_key": "模型服务密钥尚未配置。",
    "unsupported_protocol": "模型服务协议尚不支持。",
    "internal_error": "模型网关调用失败。",
    "gateway_unavailable": "无法连接模型网关。",
    "forbidden": "当前账号无权执行此模型调用。",
    "disabled": "模型或业务用途未启用。",
    "unsupported_capability": "当前模型未声明支持所需的文本或图片能力。",
}


class GatewayError(Exception):
    def __init__(self, code, message=None, status=502):
        self.code = code if code in ERROR_MESSAGES else "upstream_error"
        self.message = ERROR_MESSAGES[self.code]
        self.status = status
        super().__init__(self.message)


def validate_provider_url(value):
    try:
        parsed = urlsplit(value)
        if (value != value.strip() or parsed.scheme != "https" or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or "?" in value or "#" in value or "\\" in value or "%" in value
                or any(ord(character) < 33 or ord(character) > 126 for character in value)
                or parsed.port not in (None, 443) or any(part in (".", "..") for part in parsed.path.split("/"))):
            raise ValueError
    except (ValueError, TypeError):
        raise ValidationError("请填写不含凭据、查询参数或片段的标准 HTTPS 服务根地址，仅支持443端口。") from None


def _messages(messages, supports_vision=False):
    if isinstance(messages, list) and any(isinstance(item, dict) and isinstance(item.get('content'), list) for item in messages):
        if not supports_vision:
            raise GatewayError('unsupported_capability', status=400)
        from .model_messages import validate_messages
        try:
            validate_messages(messages, vision=True)
        except ValueError:
            raise GatewayError('invalid_request', status=400) from None
        return messages
    if not isinstance(messages, list) or not 1 <= len(messages) <= 32:
        raise GatewayError("invalid_request", status=400)
    for message in messages:
        if (not isinstance(message, dict) or set(message) != {"role", "content"}
                or message["role"] not in ("system", "user", "assistant")
                or not isinstance(message["content"], str) or not 1 <= len(message["content"]) <= 16000):
            raise GatewayError("invalid_request", status=400)
    if len(json.dumps(messages, ensure_ascii=False).encode()) > 60000:
        raise GatewayError("request_too_large", status=413)
    return messages


def _model_config(model):
    try:
        model.full_clean()
        model.provider.full_clean()
    except ValidationError:
        raise GatewayError("invalid_request", status=400) from None
    if not model.supports_text:
        raise GatewayError("unsupported_capability", status=400)
    return {"provider": {"protocol": model.provider.protocol, "base_url": model.provider.base_url,
                         "api_key_env": model.provider.api_key_env},
            "model": {"model_name": model.model_name, "token_parameter": model.token_parameter,
                      "max_output_tokens": model.max_output_tokens, "timeout_seconds": model.timeout_seconds}}


def _request_gateway(payload):
    url = settings.MODEL_GATEWAY_URL
    token = settings.MODEL_GATEWAY_TOKEN
    try:
        parsed = urlsplit(url)
        local_service = parsed.hostname in ("127.0.0.1", "::1") or url == "http://model-gateway:18410"
        if (url not in settings.MODEL_GATEWAY_ALLOWED_URLS or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ("", "/") or "\\" in url or any(ord(char) < 33 for char in url)
                or (parsed.scheme != "https" and not (parsed.scheme == "http" and local_service))
                or not parsed.port and parsed.scheme == "http" or len(token) < 40
                or any(ord(char) < 33 or ord(char) > 126 for char in token)):
            raise ValueError
    except (TypeError, ValueError):
        raise GatewayError("unconfigured", status=503) from None
    vision = any(isinstance(item['content'], list) for item in payload['messages'])
    endpoint = '/v1/generate-vision' if vision else '/v1/generate'
    request = Request(url.rstrip("/") + endpoint, data=json.dumps(payload, ensure_ascii=False).encode(),
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"}, method="POST")
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=payload["model"]["timeout_seconds"] + 5) as response:
            raw = response.read(1048577)
            if len(raw) > 1048576:
                raise GatewayError("response_too_large")
            result = json.loads(raw)
        if not isinstance(result, dict) or set(result) != {"content", "duration_ms", "prompt_tokens", "completion_tokens"}:
            raise ValueError
        if payload["purpose"] == "business" and (not isinstance(result["content"], str) or not result["content"].strip()):
            raise ValueError
        if payload["purpose"] == "test" and result["content"] is not None:
            raise ValueError
        for field in ("duration_ms", "prompt_tokens", "completion_tokens"):
            if result[field] is None and field != "duration_ms":
                continue
            if type(result[field]) is not int or not 0 <= result[field] <= 2147483647:
                raise ValueError
        return result
    except HTTPError as error:
        try:
            code = json.loads(error.read(8192)).get("code", "upstream_error")
        except (ValueError, AttributeError):
            code = "upstream_error"
        finally:
            error.close()
        statuses = {"unconfigured": 503, "unauthorized": 503, "missing_key": 503,
                    "busy": 429, "rate_limited": 429, "timeout": 504, "target_not_allowed": 403,
                    "invalid_request": 400, "request_too_large": 413, "unsupported_protocol": 400}
        raise GatewayError(code, status=statuses.get(code, 502) if isinstance(code, str) else 502) from None
    except (URLError, TimeoutError, OSError, HTTPException):
        raise GatewayError("gateway_unavailable", status=503) from None
    except (ValueError, TypeError, KeyError):
        raise GatewayError("invalid_response") from None


def _fresh_user(user):
    from .models import User
    fresh = User.objects.filter(pk=user.pk, is_active=True, must_change_password=False).first()
    if not fresh or fresh.session_version != user.session_version:
        raise GatewayError("forbidden", status=403)
    return fresh


def _route_for(user, code):
    from .models import ModelRoute
    user = _fresh_user(user)
    route = ModelRoute.objects.select_related("model__provider", "module").filter(code=code).first()
    if not route or not authorized_modules(user).filter(pk=route.module_id, enabled=True).exists():
        raise GatewayError("forbidden", status=403)
    if not route.enabled or not route.model.enabled or not route.model.provider.enabled:
        raise GatewayError("disabled", status=409)
    return user, route


def _reserve(user, model, purpose, route):
    from .models import ModelCallLog, User
    with transaction.atomic():
        User.objects.select_for_update().get(pk=user.pk)
        recent = ModelCallLog.objects.filter(actor=user, created_at__gte=timezone.now() - timedelta(minutes=1))
        if recent.count() >= 10 or (purpose == "test" and recent.filter(purpose="test", model=model).exists()):
            raise GatewayError("rate_limited", status=429)
        return ModelCallLog.objects.create(actor=user, route=route, model=model, purpose=purpose,
                                           status="pending", duration_ms=0)


def _invoke(user, model, messages, purpose, route=None):
    from .models import GatewayModel
    config = _model_config(model)
    revisions = (model.provider.updated_at, model.updated_at, route.updated_at if route else None)
    payload = {**config, "messages": _messages(messages, model.supports_vision), "purpose": purpose}
    record = _reserve(user, model, purpose, route)
    started = monotonic()
    try:
        result = _request_gateway(payload)
        fresh = _fresh_user(user)
        if fresh.grant_version != user.grant_version:
            raise GatewayError("forbidden", status=403)
        if purpose == "business":
            fresh, current = _route_for(fresh, route.code)
            if (current.model_id != model.pk or _model_config(current.model) != config
                    or (current.model.provider.updated_at, current.model.updated_at, current.updated_at) != revisions):
                raise GatewayError("disabled", status=409)
        elif not fresh.is_platform_admin:
            raise GatewayError("forbidden", status=403)
        else:
            current = GatewayModel.objects.select_related("provider").get(pk=model.pk)
            if _model_config(current) != config or (current.provider.updated_at, current.updated_at, None) != revisions:
                raise GatewayError("disabled", status=409)
        record.status = "success"
        record.prompt_tokens = result["prompt_tokens"]
        record.completion_tokens = result["completion_tokens"]
        return result
    except GatewayError as error:
        record.status = error.code
        raise
    except Exception:
        record.status = "internal_error"
        raise GatewayError("internal_error") from None
    finally:
        record.duration_ms = max(0, int((monotonic() - started) * 1000))
        record.save(update_fields=["status", "duration_ms", "prompt_tokens", "completion_tokens"])
        audit(user, "model_test" if purpose == "test" else "model_call", record.pk, result=record.status)


def generate_for_use(user, purpose_code, messages):
    user, route = _route_for(user, purpose_code)
    return _invoke(user, route.model, messages, "business", route)


def test_connection(user, model_id):
    from .models import GatewayModel
    user = _fresh_user(user)
    if not user.is_platform_admin:
        raise GatewayError("forbidden", status=403)
    model = GatewayModel.objects.select_related("provider").filter(pk=model_id).first()
    if not model:
        raise GatewayError("invalid_request", status=404)
    return _invoke(user, model, [{"role": "user", "content": "Reply with OK."}], "test")
