"""Owner-scoped HTTP endpoints; optimistic lease prevents overlapping turns on SQLite too."""
import uuid
from datetime import timedelta
from functools import wraps

from django.db.models import Q, F
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


@api_view(["GET"])
@endpoint
def status(request):
    try:
        service.ready(request.user)
        return Response({"available": True, "code": "ready", "help": "基于已授权产品资料回答；Enter 发送，Shift+Enter 换行。"})
    except ProductError as error:
        return Response({"available": False, "code": error.code, "help": error.detail})


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
    data = body(request, ("question", "version", "request_id"))
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


urlpatterns = [path("knowledge/status/", status), path("knowledge/conversations/", conversations),
               path("knowledge/conversations/<uuid:conversation_id>/", conversation)]
