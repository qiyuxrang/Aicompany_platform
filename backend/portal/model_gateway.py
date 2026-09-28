import json
import re
import uuid
from contextlib import closing
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
from django.utils.crypto import salted_hmac

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
    "model_configuration_changed": "模型配置已更新，请刷新模型列表后重试。",
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
        local_http = (settings.DEBUG and settings.MODEL_PROVIDER_LOCAL_HTTP
                      and re.fullmatch(r"http://127\.0\.0\.1:19880/api/v1/openai/[0-9a-f]{32}", value))
        if (not local_http and (value != value.strip() or parsed.scheme != "https" or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or "?" in value or "#" in value or "\\" in value or "%" in value
                or any(ord(character) < 33 or ord(character) > 126 for character in value)
                or parsed.port not in (None, 443) or any(part in (".", "..") for part in parsed.path.split("/")))):
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


def _gateway_request(payload, endpoint=None):
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
    if endpoint is None:
        vision = any(isinstance(item['content'], list) for item in payload['messages'])
        endpoint = '/v1/generate-vision' if vision else '/v1/generate'
    request = Request(url.rstrip("/") + endpoint, data=json.dumps(payload, ensure_ascii=False).encode(),
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"}, method="POST")
    return request, payload.get("model", {}).get("timeout_seconds", 15) + 5


def fetch_model_catalog(provider):
    provider.full_clean()
    request, timeout = _gateway_request({"provider": {
        "protocol": provider.protocol, "base_url": provider.base_url,
        "api_key_env": provider.api_key_env,
    }}, "/v1/models")
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout) as response:
            body = response.read(262145)
        if len(body) > 262144:
            raise GatewayError("response_too_large")
        data = json.loads(body)
        identifiers = data.get("models") if isinstance(data, dict) else None
        if (not isinstance(identifiers, list) or len(identifiers) > 500
                or any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", name)
                       for name in identifiers) or len(identifiers) != len(set(identifiers))):
            raise ValueError
        return identifiers
    except HTTPError as error:
        try:
            code = json.loads(error.read(8192)).get("code", "upstream_error")
        except (ValueError, AttributeError):
            code = "upstream_error"
        finally:
            error.close()
        raise GatewayError(code if isinstance(code, str) and code in ERROR_MESSAGES else "upstream_error") from None
    except (URLError, TimeoutError, OSError, HTTPException):
        raise GatewayError("gateway_unavailable", status=503) from None
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise GatewayError("invalid_response") from None


def _request_gateway(payload):
    request, timeout = _gateway_request(payload)
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout) as response:
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


def _stream_events(response, deadline):
    buffer = bytearray()
    total = 0
    done = False
    while True:
        if monotonic() >= deadline:
            raise GatewayError("timeout", status=504)
        chunk = response.readline(65537)
        if not chunk:
            break
        total += len(chunk)
        if total > 1048576:
            raise GatewayError("response_too_large")
        buffer.extend(chunk)
        buffer = bytearray(buffer.replace(b"\r\n", b"\n"))
        while b"\n\n" in buffer:
            frame, _, remainder = buffer.partition(b"\n\n")
            buffer = bytearray(remainder)
            lines = frame.replace(b"\r\n", b"\n").split(b"\n")
            data = [line[5:].lstrip(b" ") for line in lines if line.startswith(b"data:")]
            if len(data) != 1 or any(line and not line.startswith((b"data:", b":")) for line in lines):
                raise GatewayError("invalid_response")
            try:
                event = json.loads(data[0].decode("utf-8"))
            except (ValueError, UnicodeError):
                raise GatewayError("invalid_response") from None
            if not isinstance(event, dict) or done:
                raise GatewayError("invalid_response")
            if set(event) == {"delta"} and isinstance(event["delta"], str) and event["delta"]:
                yield event
            elif set(event) == {"done", "prompt_tokens", "completion_tokens"} and event["done"] is True:
                if any(event[key] is not None and (type(event[key]) is not int or not 0 <= event[key] <= 2147483647)
                       for key in ("prompt_tokens", "completion_tokens")):
                    raise GatewayError("invalid_response")
                done = True
                terminal = event
            elif (set(event) == {"error"} and isinstance(event["error"], dict)
                  and isinstance(event["error"].get("code"), str)
                  and set(event["error"]) == {"code", "detail"}
                  and isinstance(event["error"]["detail"], str)):
                raise GatewayError(event["error"]["code"])
            else:
                raise GatewayError("invalid_response")
        if len(buffer) > 65536:
            raise GatewayError("response_too_large")
    if buffer or not done or monotonic() >= deadline:
        raise GatewayError("invalid_response" if buffer or not done else "timeout")
    yield terminal


def _request_gateway_stream(payload):
    request, timeout = _gateway_request(payload, "/v1/generate-stream")
    response = None
    try:
        response = build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout)
        if response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "text/event-stream":
            raise GatewayError("invalid_response")
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise GatewayError("invalid_response")
        yield from _stream_events(response, monotonic() + timeout)
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
    finally:
        if response is not None:
            response.close()


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
    # A route is only valid while its configured default remains usable. Optional
    # models supplement the default; they do not mask a broken route baseline.
    if not route.enabled or not route.model.enabled or not route.model.provider.enabled:
        raise GatewayError("disabled", status=409)
    return user, route


def _configuration_version(route, model, option=None):
    """Return an opaque revision without exposing provider or remote model data."""
    payload = {
        "route": [route.pk, route.code, route.module_id, route.model_id, route.enabled,
                  route.max_calls_per_minute],
        "route_updated": route.updated_at.isoformat(),
        "model": [str(model.public_id), model.provider_id, model.model_name, model.supports_text,
                  model.supports_vision, model.enabled, model.timeout_seconds,
                  model.max_output_tokens, model.token_parameter],
        "model_updated": model.updated_at.isoformat(),
        "provider": [model.provider.protocol, model.provider.base_url,
                     model.provider.api_key_env, model.provider.enabled],
        "provider_updated": model.provider.updated_at.isoformat(),
        "option": [option.pk, option.model_id, option.enabled, option.display_order] if option else None,
        "option_updated": option.updated_at.isoformat() if option else None,
        "roles": sorted(option.allowed_roles.values_list("pk", flat=True)) if option else [],
        "users": sorted(option.allowed_users.values_list("pk", flat=True)) if option else [],
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return salted_hmac("portal.model-selection", serialized, algorithm="sha256").hexdigest()


def _available_route_models(user, route):
    """Return enabled models authorized by route, role and direct-user scopes."""
    from .models import ModelRouteOption

    role_ids = set(user.roles.values_list("pk", flat=True))
    options = list(
        ModelRouteOption.objects.filter(route=route)
        .select_related("model__provider")
        .prefetch_related("allowed_roles", "allowed_users")
        .order_by("display_order", "id")
    )
    by_model = {option.model_id: option for option in options}
    choices = []
    for option in options:
        allowed_roles = {role.pk for role in option.allowed_roles.all()}
        allowed_users = {allowed.pk for allowed in option.allowed_users.all()}
        scoped = bool(allowed_roles or allowed_users)
        if (option.enabled and option.model.enabled and option.model.provider.enabled
                and (not scoped or user.pk in allowed_users or bool(role_ids & allowed_roles))):
            choices.append((option.model, option))
    # Backward compatibility: existing routes need no option row for their default.
    if route.model_id not in by_model and route.model.enabled and route.model.provider.enabled:
        choices.insert(0, (route.model, None))
    # A model can only appear once even if legacy data is unusual.
    unique = {}
    for model, option in choices:
        unique.setdefault(model.pk, (model, option))
    return list(unique.values())


def _normalize_selection(selection):
    if selection is None:
        return None, None
    if not isinstance(selection, dict) or set(selection) - {"model_id", "config_version"}:
        raise GatewayError("invalid_request", status=400)
    model_id, version = selection.get("model_id"), selection.get("config_version")
    if not isinstance(model_id, str) or not isinstance(version, str) or len(version) != 64:
        raise GatewayError("invalid_request", status=400)
    try:
        model_id = uuid.UUID(model_id)
        int(version, 16)
    except (ValueError, TypeError):
        raise GatewayError("invalid_request", status=400) from None
    return model_id, version.lower()


def _select_route_model(user, route, selection=None):
    requested_id, requested_version = _normalize_selection(selection)
    choices = _available_route_models(user, route)
    if requested_id is None:
        selected = next((choice for choice in choices if choice[0].pk == route.model_id), None)
        selected = selected or (choices[0] if choices else None)
    else:
        selected = next((choice for choice in choices if choice[0].public_id == requested_id), None)
    if not selected:
        # Do not reveal whether an unknown model exists or merely lacks authorization.
        if requested_id is not None:
            raise GatewayError("forbidden", status=403)
        raise GatewayError("disabled", status=409)
    model, option = selected
    version = _configuration_version(route, model, option)
    if requested_version is not None and requested_version != version:
        raise GatewayError("model_configuration_changed", status=409)
    return model, option, version


def selectable_models(user, purpose_code):
    """Safe metadata for a UI model picker; secrets and remote identifiers stay server-side."""
    user, route = _route_for(user, purpose_code)
    choices = [choice for choice in _available_route_models(user, route) if choice[0].supports_text]
    if not choices:
        raise GatewayError("disabled", status=409)
    default = next((choice for choice in choices if choice[0].pk == route.model_id), None)
    default = default or (choices[0] if choices else None)
    models = []
    for model, option in choices:
        models.append({
            "id": str(model.public_id),
            "name": model.name,
            "capabilities": {"text": model.supports_text, "vision": model.supports_vision},
            "max_output_tokens": model.max_output_tokens,
            "is_default": bool(default and default[0].pk == model.pk),
            "config_version": _configuration_version(route, model, option),
        })
    return {
        "route": {"code": route.code, "name": route.name, "module": route.module.code},
        "default_model_id": str(default[0].public_id) if default else None,
        "models": models,
    }


def validate_model_selection(user, purpose_code, selection=None, required_capability="text"):
    """Resolve and pin a selection before persisting an asynchronous task."""
    if required_capability not in {"text", "vision"}:
        raise GatewayError("invalid_request", status=400)
    user, route = _route_for(user, purpose_code)
    model, option, version = _select_route_model(user, route, selection)
    if not model.supports_text or (required_capability == "vision" and not model.supports_vision):
        raise GatewayError("unsupported_capability", status=400)
    return {"model_id": str(model.public_id), "config_version": version}


def _reserve(user, model, purpose, route, config_version=""):
    from .models import GatewayModel, ModelCallLog, User
    with transaction.atomic():
        User.objects.select_for_update().get(pk=user.pk)
        GatewayModel.objects.select_for_update().get(pk=model.pk)
        recent = ModelCallLog.objects.filter(actor=user, created_at__gte=timezone.now() - timedelta(minutes=1))
        limit = route.max_calls_per_minute if route else 10
        pending_since = timezone.now() - timedelta(seconds=model.timeout_seconds + 30)
        pending = ModelCallLog.objects.filter(model=model, status="pending", created_at__gte=pending_since).count()
        max_pending = getattr(settings, "MODEL_MAX_PENDING_PER_MODEL", 4)
        if pending >= max_pending:
            raise GatewayError("busy", status=429)
        route_recent = recent.filter(route=route) if route else recent
        if route_recent.count() >= limit or (purpose == "test" and recent.filter(purpose="test", model=model).exists()):
            raise GatewayError("rate_limited", status=429)
        return ModelCallLog.objects.create(actor=user, route=route, model=model, purpose=purpose,
                                           status="pending", duration_ms=0, model_public_id=model.public_id,
                                           config_version=config_version)


def _invoke(user, model, messages, purpose, route=None, config_version="", selection_pinned=False):
    from .models import GatewayModel
    config = _model_config(model)
    revisions = (model.provider.updated_at, model.updated_at, route.updated_at if route else None)
    payload = {**config, "messages": _messages(messages, model.supports_vision), "purpose": purpose}
    record = _reserve(user, model, purpose, route, config_version)
    started = monotonic()
    try:
        result = _request_gateway(payload)
        fresh = _fresh_user(user)
        if fresh.grant_version != user.grant_version:
            raise GatewayError("forbidden", status=403)
        if purpose == "business":
            fresh, current = _route_for(fresh, route.code)
            try:
                current_model, current_option, current_version = _select_route_model(fresh, current, {
                    "model_id": str(model.public_id), "config_version": config_version,
                })
            except GatewayError as error:
                if error.code == "model_configuration_changed" and not selection_pinned:
                    raise GatewayError("disabled", status=409) from None
                raise
            if (_model_config(current_model) != config or current_version != config_version
                    or (current_model.provider.updated_at, current_model.updated_at, current.updated_at) != revisions):
                raise GatewayError("model_configuration_changed" if selection_pinned else "disabled", status=409)
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


def generate_for_use(user, purpose_code, messages, model_selection=None):
    user, route = _route_for(user, purpose_code)
    model, option, config_version = _select_route_model(user, route, model_selection)
    return _invoke(user, model, messages, "business", route, config_version,
                   selection_pinned=model_selection is not None)


def stream_for_use(user, purpose_code, messages):
    if purpose_code != getattr(settings, "PRODUCT_KNOWLEDGE_MODEL_ROUTE", None):
        raise GatewayError("forbidden", status=403)
    user, route = _route_for(user, purpose_code)
    if route.module.code != "product":
        raise GatewayError("forbidden", status=403)
    model, option, config_version = _select_route_model(user, route)
    config = _model_config(model)
    revisions = (model.provider.updated_at, model.updated_at, route.updated_at)
    payload = {**config, "messages": _messages(messages), "purpose": "business"}
    record = _reserve(user, model, "business", route, config_version)
    started = monotonic()
    try:
        completed = False
        with closing(_request_gateway_stream(payload)) as stream:
            for event in stream:
                if "done" not in event:
                    yield event
                    continue
                fresh = _fresh_user(user)
                if fresh.grant_version != user.grant_version:
                    raise GatewayError("forbidden", status=403)
                fresh, current = _route_for(fresh, route.code)
                try:
                    current_model, current_option, current_version = _select_route_model(fresh, current, {
                        "model_id": str(model.public_id), "config_version": config_version,
                    })
                except GatewayError as error:
                    if error.code == "model_configuration_changed":
                        raise GatewayError("disabled", status=409) from None
                    raise
                if (_model_config(current_model) != config or current_version != config_version
                        or (current_model.provider.updated_at, current_model.updated_at, current.updated_at) != revisions):
                    raise GatewayError("disabled", status=409)
                record.status = "success"
                record.prompt_tokens = event["prompt_tokens"]
                record.completion_tokens = event["completion_tokens"]
                completed = True
                yield event
        if not completed:
            raise GatewayError("invalid_response")
    except GatewayError as error:
        record.status = error.code
        raise
    except GeneratorExit:
        record.status = "cancelled"
        raise
    except Exception:
        record.status = "internal_error"
        raise GatewayError("internal_error") from None
    finally:
        if record.status == "pending":
            record.status = "invalid_response"
        record.duration_ms = max(0, int((monotonic() - started) * 1000))
        record.save(update_fields=["status", "duration_ms", "prompt_tokens", "completion_tokens"])
        audit(user, "model_call", record.pk, result=record.status)


def test_connection(user, model_id):
    from .models import GatewayModel
    user = _fresh_user(user)
    if not user.is_platform_admin:
        raise GatewayError("forbidden", status=403)
    model = GatewayModel.objects.select_related("provider").filter(pk=model_id).first()
    if not model:
        raise GatewayError("invalid_request", status=404)
    return _invoke(user, model, [{"role": "user", "content": "Reply with OK."}], "test")
