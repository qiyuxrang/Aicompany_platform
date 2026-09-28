"""Owner-scoped HTTP endpoints; optimistic lease prevents overlapping turns on SQLite too."""
import json
import uuid
from datetime import timedelta
from functools import wraps

from django.db.models import Q, F
from django.http import StreamingHttpResponse
from django.urls import path
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.exceptions import ParseError
from rest_framework.response import Response

from .product_knowledge_models import ProductKnowledgeConversation as Conversation
from . import product_knowledge_service as service
from .product_service import ProductError


def endpoint(function):
    @wraps(function)
    def wrapped(request, *args, **kwargs):
        try:
            request.knowledge_scope = service.authorize(request.user)
            response = function(request, *args, **kwargs)
        except ProductError as error:
            response = Response({"code": error.code, "detail": error.detail}, status=error.status)
        except ParseError:
            response = Response({"code": "invalid_request", "detail": "请求 JSON 格式无效。"}, status=400)
        response["Cache-Control"] = "private, no-store"
        return response
    return wrapped


def body(request, keys):
    if not isinstance(request.data, dict) or set(request.data) != set(keys):
        raise ProductError("invalid_request", "请求字段无效。", 400)
    return request.data


def visible(request, conversation):
    service.recheck(request.user, request.knowledge_scope)
    if conversation.scope != request.knowledge_scope:
        service.fail("scope_revoked", 403)


def summary(conversation):
    return {"id": str(conversation.pk), "title": conversation.title, "version": conversation.version}


def detail(request, conversation):
    visible(request, conversation)
    return {**summary(conversation), "turns": conversation.turns}


def stream_event(payload):
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def stream_turn(request, item, claim, question, request_id, version, config):
    from .model_gateway import GatewayError, stream_for_use
    try:
        item.refresh_from_db()
        visible(request, item)
        sources = service.retrieve(question, item.turns, item.scope, config)
        visible(request, item)
        if service.ready(request.user) != config:
            service.fail("unconfigured")
        if sources:
            raw, preview, completed = "", "", False
            events = stream_for_use(request.user, config[2], service.answer_messages(question, item.turns, sources))
            try:
                for event in events:
                    if "delta" in event and type(event["delta"]) is str:
                        raw += event["delta"]
                        if len(raw) > 12000:
                            service.fail("invalid_response", 502)
                        current = service.preview_answer(raw)
                        if not current.startswith(preview):
                            service.fail("invalid_response", 502)
                        if len(current) > len(preview):
                            yield stream_event({"delta": current[len(preview):]})
                            preview = current
                    elif event.get("done") is True:
                        completed = True
                    else:
                        service.fail("invalid_response", 502)
            finally:
                if hasattr(events, "close"):
                    events.close()
            if not completed:
                service.fail("invalid_response", 502)
            answer, sources = service.parse_answer(raw, sources)
            outcome = "answered"
        else:
            answer, outcome = "已授权资料中未检索到相关内容，请补充具体设备或项目名称后重试。", "empty"
            yield stream_event({"delta": answer})
        visible(request, item)
        if service.ready(request.user) != config:
            service.fail("unconfigured")
        turns = item.turns + [{"question": question, "answer": answer, "sources": sources,
                               "outcome": outcome, "request_id": request_id}]
        saved = Conversation.objects.filter(pk=item.pk, owner=request.user, version=version, pending_id=claim,
                                            pending_until__gt=timezone.now()).update(
            turns=turns, title=question[:100] if not item.turns else item.title,
            version=F("version") + 1, pending_id=None, pending_until=None, updated_at=timezone.now())
        if not saved:
            service.fail("conflict", 409)
        item.refresh_from_db()
        yield stream_event({"done": detail(request, item)})
    except ProductError as error:
        yield stream_event({"error": {"code": error.code, "detail": error.detail}})
    except GatewayError as error:
        yield stream_event({"error": {"code": error.code, "detail": error.message}})
    except Exception:
        yield stream_event({"error": {"code": "unavailable", "detail": "知识服务暂时不可用，请稍后重试。"}})
    finally:
        Conversation.objects.filter(pk=item.pk, pending_id=claim).update(pending_id=None, pending_until=None)


@api_view(["GET"])
@endpoint
def status(request):
    try:
        service.ready(request.user)
        return Response({"available": True, "code": "ready", "help": "基于已授权产品资料回答；Enter 发送，Shift+Enter 换行。"})
    except ProductError as error:
        return Response({"available": False, "code": error.code, "help": error.detail})


@api_view(["GET"])
@endpoint
def datasets(request):
    config = service.ready(request.user)
    items = service.list_datasets(request.knowledge_scope, config)
    service.recheck(request.user, request.knowledge_scope)
    return Response({"datasets": items})


@api_view(["GET", "POST"])
@endpoint
def conversations(request):
    if request.method == "GET":
        # Filter before emitting titles: even a conversation's first question can contain secrets.
        items = [summary(item) for item in Conversation.objects.filter(owner=request.user).only("id", "title", "version", "scope")[:100]
                 if item.scope == request.knowledge_scope]
        service.recheck(request.user, request.knowledge_scope)
        return Response({"conversations": items})
    body(request, ())
    service.ready(request.user)
    if Conversation.objects.filter(owner=request.user).count() >= 100:
        raise ProductError("conversation_limit", "最多保留100个对话，请联系管理员归档。", 409)
    service.recheck(request.user, request.knowledge_scope)
    conversation = Conversation.objects.create(owner=request.user, scope=request.knowledge_scope)
    return Response(detail(request, conversation), status=201)


@api_view(["GET", "POST"])
@endpoint
def conversation(request, conversation_id):
    item = Conversation.objects.filter(pk=conversation_id, owner=request.user).first()
    if item is None:
        raise ProductError("not_found", "对象不存在。", 404)
    visible(request, item)
    if request.method == "GET":
        return Response(detail(request, item))
    streaming = isinstance(request.data, dict) and request.data.get("stream") is True
    data = body(request, ("question", "version", "request_id", "stream") if streaming
                else ("question", "version", "request_id"))
    question = data["question"]
    if (not isinstance(question, str) or not question.strip() or len(question) > 2000
            or any((ord(c) < 32 and c not in "\r\n\t") or 0xD800 <= ord(c) <= 0xDFFF for c in question)
            or type(data["version"]) is not int or data["version"] < 0):
        raise ProductError("invalid_request", "问题须为1–2000字符，version须为非负整数。", 400)
    question = question.strip()
    try:
        request_id = str(uuid.UUID(data["request_id"]))
    except (ValueError, TypeError, AttributeError):
        raise ProductError("invalid_request", "request_id须为UUID。", 400) from None
    for turn in item.turns:
        if turn["request_id"] == request_id:
            if turn["question"] != question:
                service.fail("conflict", 409)
            if streaming:
                return StreamingHttpResponse(iter([stream_event({"done": detail(request, item)})]),
                                             content_type="text/event-stream; charset=utf-8",
                                             headers={"X-Accel-Buffering": "no"})
            return Response(detail(request, item))
    if len(item.turns) >= 40:
        raise ProductError("history_limit", "此对话已达40轮，请新建对话。", 409)
    config = service.ready(request.user)
    now = timezone.now()
    claim = uuid.uuid4()
    # Single conditional UPDATE, rather than select_for_update (ineffective on SQLite).
    claimed = (Conversation.objects.filter(pk=item.pk, owner=request.user, version=data["version"])
               .filter(Q(pending_id__isnull=True) | Q(pending_until__lt=now))
               .update(pending_id=claim, pending_until=now + timedelta(seconds=180)))
    if not claimed:
        service.fail("conflict", 409)
    if streaming:
        return StreamingHttpResponse(stream_turn(request, item, claim, question, request_id, data["version"], config),
                                     content_type="text/event-stream; charset=utf-8",
                                     headers={"X-Accel-Buffering": "no"})
    try:
        item.refresh_from_db()
        visible(request, item)
        sources = service.retrieve(question, item.turns, item.scope, config)
        visible(request, item)
        # Recheck switches/route immediately before sending private evidence to the gateway.
        if service.ready(request.user) != config:
            service.fail("unconfigured")
        visible(request, item)
        if sources:
            answer, sources = service.answer(request.user, question, item.turns, sources, config)
            outcome = "answered"
        else:
            answer, outcome = "已授权资料中未检索到相关内容，请补充具体设备或项目名称后重试。", "empty"
        visible(request, item)
        if service.ready(request.user) != config:
            service.fail("unconfigured")
        visible(request, item)
        turns = item.turns + [{"question": question, "answer": answer, "sources": sources,
                               "outcome": outcome, "request_id": request_id}]
        saved = Conversation.objects.filter(pk=item.pk, owner=request.user, version=data["version"], pending_id=claim, pending_until__gt=timezone.now()).update(
            turns=turns, title=question[:100] if not item.turns else item.title,
            version=F("version") + 1, pending_id=None, pending_until=None, updated_at=timezone.now())
        if not saved:
            service.fail("conflict", 409)
        item.refresh_from_db()
        return Response(detail(request, item))
    finally:
        # An expired worker cannot release a newer worker's lease or overwrite its result.
        Conversation.objects.filter(pk=item.pk, pending_id=claim).update(pending_id=None, pending_until=None)


urlpatterns = [path("knowledge/status/", status), path("knowledge/datasets/", datasets),
               path("knowledge/conversations/", conversations),
               path("knowledge/conversations/<uuid:conversation_id>/", conversation)]
