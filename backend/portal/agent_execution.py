import copy
from datetime import timedelta
from urllib.parse import urlsplit

from asgiref.sync import async_to_sync
from .agent_db import database_sync_to_async as sync_to_async
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .agent_models import AgentConversation, AgentMessage, AgentRun, AgentWorkTask, append_public_event
from .agent_runtime import AgentDenied, RunBinding, RuntimeGuard, bind_run, initialize_root


def runtime_client(binding):
    from langgraph_sdk import get_client
    url = settings.AGENT_RUNTIME_URL
    secret = settings.AGENT_RUNTIME_SERVICE_TOKEN
    try:
        parsed = urlsplit(url)
        if (url not in settings.AGENT_RUNTIME_ALLOWED_URLS or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in ("", "/")
                or not parsed.hostname or "\\" in url
                or any(ord(character) < 33 or ord(character) > 126 for character in url)
                or parsed.scheme not in ("https", "http")
                or parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "::1")
                or not isinstance(secret, str) or len(secret) < 40
                or any(ord(character) < 33 or ord(character) > 126 for character in secret)):
            raise ValueError
    except (ValueError, TypeError):
        raise AgentDenied("runtime_unconfigured") from None
    return get_client(url=url, api_key=None, timeout=15,
        headers={"Authorization": "Bearer " + secret, "X-Agent-Binding": binding.token()})


def prepare_root(message):
    with transaction.atomic():
        root = AgentRun.objects.select_for_update().get(pk=message.conversation.root_run_id)
        if not root.policy and not root.action_count and root.state == "pending":
            root.policy = {**copy.deepcopy(settings.AGENT_ROOT_POLICY),
                           "configuration_source": "PORTAL_AGENT_ROOT_POLICY"}
            root.deadline_at = root.created_at + timedelta(seconds=root.policy["deadline_seconds"])
            root.save(update_fields=["policy", "deadline_at"])
    if "identity" not in root.policy:
        return initialize_root(root.pk, message.conversation.owner_id)
    return bind_run(root.pk, message.conversation.owner_id)


async def _dispatch(message, guard, client):
    from .agent_runtime import NativeRuntime
    native = NativeRuntime(guard, client, set())
    root, _ = await sync_to_async(guard._load)()
    operation = "message:" + str(message.pk)
    if root.state == "dispatch_unknown":
        await native.recover_dispatches()
        root, _ = await sync_to_async(guard._load)()
    if not root.native_run_id or root.policy.get("dispatch_key") == operation:
        await native.start(message.content, operation, graph_id=settings.AGENT_MAIN_GRAPH)
    else:
        await native.resume(message.content, operation)
    await sync_to_async(append_public_event)(root.pk, root.pk, operation + ":submitted",
        "message_submitted", {"message_id": str(message.pk)}, work=message.work)
    return {"state": "submitted", "message_id": str(message.pk)}


def dispatch_message(message_id):
    message = AgentMessage.objects.select_related("conversation", "work").get(pk=message_id, role="user")
    if not getattr(settings, "AGENT_PLATFORM_ENABLED", False):
        return {"state": "unavailable", "message_id": str(message.pk)}
    if not settings.AGENT_RUNTIME_URL:
        return {"state": "runtime_unconfigured", "message_id": str(message.pk)}
    try:
        binding = prepare_root(message)
        guard = RuntimeGuard(binding)
        async def submit():
            async with runtime_client(binding) as client:
                return await _dispatch(message, guard, client)
        return async_to_sync(submit)()
    except AgentDenied as error:
        return {"state": "blocked", "reason": str(error), "message_id": str(message.pk)}
    except Exception:
        return {"state": "dispatch_unknown", "message_id": str(message.pk)}


def cancel_root(root_id):
    from .agent_runtime import NativeRuntime
    root = AgentRun.objects.select_related("conversation").get(pk=root_id, parent__isnull=True)
    if root.state != "stopping":
        return {"state": root.state}
    identity = root.policy.get("identity", {})
    binding = RunBinding(root.conversation.owner_id, str(root.pk), str(root.pk),
        identity.get("grant_version"), identity.get("session_version"), root.policy.get("fence"))
    if root.native_run_id or root.policy.get("dispatches"):
        if not settings.AGENT_RUNTIME_URL:
            return {"state": "stopping", "reason": "runtime_unconfigured"}
        async def interrupt():
            async with runtime_client(binding) as client:
                return await NativeRuntime(RuntimeGuard(binding), client, {"researcher", "reviewer"}).reconcile()
        try:
            async_to_sync(interrupt)()
        except Exception:
            return {"state": "stopping", "reason": "native_cancel_unconfirmed"}
    with transaction.atomic():
        root = AgentRun.objects.select_for_update().get(pk=root.pk)
        if root.state != "stopping":
            return {"state": root.state}
        if (any(intent.get("state") in {"reserved", "sending"} for intent in root.policy.get("dispatches", {}).values())
                or root.policy.get("active")
                or AgentRun.objects.filter(root_run_id=root.pk, parent__isnull=False).exclude(
                    state__in=["cancelled", "completed", "failed", "terminated"]).exists()):
            return {"state": "stopping", "reason": "native_or_domain_work_pending"}
        root.state = "cancelled"
        root.save(update_fields=["state", "updated_at"])
        AgentWorkTask.objects.filter(pk=root.work_id, state="stopping").update(state="cancelled")
        append_public_event(root.pk, root.pk, "cancelled:" + str(root.work_id),
            "cancelled", {"work_id": str(root.work_id)}, work=root.work)
    return {"state": "cancelled"}


def reconcile_conversation(conversation_id, owner_id):
    conversation = AgentConversation.objects.get(pk=conversation_id, owner_id=owner_id)
    root = AgentRun.objects.get(pk=conversation.root_run_id)
    if root.state == "stopping":
        return cancel_root(root.pk)
    if not root.native_thread_id or not root.native_run_id or not settings.AGENT_RUNTIME_URL:
        return {"state": root.state}
    binding = bind_run(root.pk, owner_id)
    guard = RuntimeGuard(binding)
    async def reconcile():
        async with runtime_client(binding) as client:
            status = await client.runs.get(root.native_thread_id, root.native_run_id)
            if status["status"] != "success":
                return {"state": status["status"]}
            state = await client.threads.get_state(root.native_thread_id)
            await sync_to_async(guard.check)()
            messages = state.get("values", {}).get("messages", [])
            answer = next((item for item in reversed(messages) if isinstance(item, dict)
                and item.get("type", item.get("role")) in ("ai", "assistant")
                and not item.get("tool_calls") and isinstance(item.get("content"), str)), None)
            if answer is None:
                return {"state": "waiting_input"}
            await sync_to_async(AgentMessage.objects.get_or_create)(conversation=conversation,
                client_request_id="native:" + root.native_run_id,
                defaults={"role": "assistant", "content": answer["content"], "work_id": root.work_id})
            await sync_to_async(AgentRun.objects.filter(pk=root.pk, state="running").update)(state="waiting_input")
            return {"state": "waiting_input"}
    return async_to_sync(reconcile)()
