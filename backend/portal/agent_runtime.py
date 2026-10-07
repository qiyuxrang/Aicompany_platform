"""Authorization and durable admission around the native LangGraph service."""

import asyncio
import copy
import uuid
import hmac
import hashlib
import json
import logging
from dataclasses import dataclass
from contextvars import ContextVar
from contextlib import asynccontextmanager, suppress

from .agent_db import database_sync_to_async as sync_to_async
from django.core import signing
from django.db import transaction
from django.utils import timezone
from langgraph_sdk import Auth


TERMINAL = {"stopping", "cancelled", "terminated", "failed", "completed"}
LIMITS = ("max_actions", "max_model_calls", "max_tool_calls", "max_launches",
          "max_concurrent", "max_active_ms")

auth = Auth()
authorized_native_binding = ContextVar("authorized_native_binding", default=None)


def setup_runtime_django():
    import django
    from django.apps import apps
    if not apps.ready:
        django.setup()


@auth.authenticate
async def authenticate_native(authorization, headers, path, method):
    await sync_to_async(setup_runtime_django)()
    from django.conf import settings
    expected = getattr(settings, "AGENT_RUNTIME_SERVICE_TOKEN", "")
    if len(expected) < 40 or not hmac.compare_digest(authorization or "", "Bearer " + expected):
        raise Auth.exceptions.HTTPException(status_code=401, detail="service_identity_required")
    raw = headers.get(b"x-agent-binding", b"")
    try:
        binding = RunBinding.from_token(raw.decode("ascii"))
        maintenance = method == "POST" and path.endswith("/cancel")
        await sync_to_async(RuntimeGuard(binding).check)(maintenance=maintenance)
    except (AgentDenied, UnicodeError):
        raise Auth.exceptions.HTTPException(status_code=403, detail="invalid_scope") from None
    authorized_native_binding.set(binding)
    return {"identity": binding.root_id, "binding": binding.__dict__}


@auth.on
async def deny_native_default(ctx, value):
    return False


@auth.on.threads
async def authorize_native_thread(ctx, value):
    root_id = ctx.user.identity
    bound = ctx.user["binding"]
    authorized_native_binding.set(RunBinding(**bound))
    thread_id = value.get("thread_id")
    if thread_id:
        AgentRun, _, _ = _models()
        exists = await sync_to_async(AgentRun.objects.filter(root_run_id=root_id,
            native_thread_id=str(thread_id)).exists)()
        if not exists:
            return False
        if bound["run_id"] != root_id and str(thread_id) != bound["run_id"]:
            return False
    if ctx.action in {"create", "create_run", "update"}:
        value.setdefault("metadata", {})["root_id"] = root_id
        if thread_id:
            value["metadata"]["platform_run_id"] = str(thread_id)
    return {"root_id": root_id, **({"platform_run_id": bound["run_id"]} if bound["run_id"] != root_id else {})}


@auth.on.assistants.read
async def authorize_native_assistant(ctx, value):
    return True


@auth.on.assistants.search
async def authorize_native_assistants(ctx, value):
    return True


class AgentDenied(PermissionError):
    pass


@dataclass(frozen=True)
class RunBinding:
    owner_id: int
    root_id: str
    run_id: str
    grant_version: int
    session_version: int
    fence: int

    def token(self):
        return signing.dumps(self.__dict__, salt="agent-runtime-binding", compress=True)

    @classmethod
    def from_token(cls, token):
        try:
            return cls(**signing.loads(token, salt="agent-runtime-binding"))
        except (signing.BadSignature, TypeError, ValueError):
            raise AgentDenied("invalid_binding") from None


def _models():
    from .agent_models import AgentRun, AgentRootAction, AgentEvent
    return AgentRun, AgentRootAction, AgentEvent


def bind_run(run_id, owner_id):
    AgentRun, _, _ = _models()
    run = AgentRun.objects.select_related("conversation__owner").get(pk=run_id)
    root = AgentRun.objects.get(pk=run.root_run_id, parent__isnull=True)
    snapshot = root.policy.get("identity", {})
    binding = RunBinding(owner_id, str(root.pk), str(run.pk), snapshot.get("grant_version"),
                         snapshot.get("session_version"), root.policy.get("fence"))
    RuntimeGuard(binding).check()
    return binding


def initialize_root(root_id, owner_id):
    from .agent_skills import skill_bundle
    AgentRun, _, _ = _models()
    with transaction.atomic():
        root = AgentRun.objects.select_for_update().select_related("conversation__owner").get(pk=root_id)
        user = root.conversation.owner
        if (root.parent_id or str(root.root_run_id) != str(root.pk) or user.pk != owner_id
                or not user.is_active or user.must_change_password or root.action_count
                or root.state in TERMINAL or not root.deadline_at or timezone.now() >= root.deadline_at):
            raise AgentDenied("invalid_root")
        policy = copy.deepcopy(root.policy)
        if any(type(policy.get(key)) is not int or policy[key] <= 0 for key in LIMITS):
            raise AgentDenied("finite_policy_required")
        if "identity" not in policy:
            policy.update(identity={"owner_id": owner_id, "grant_version": user.grant_version,
                                   "session_version": user.session_version},
                          fence=1, active={}, active_ms=0)
            policy["skill_digest"] = skill_bundle(owner_id)["digest"]
            root.policy = policy
            root.save(update_fields=["policy"])
    return bind_run(root_id, owner_id)


class RuntimeGuard:
    def __init__(self, binding):
        self.binding = binding

    def _authorize(self, root, run, *, maintenance=False):
        user = root.conversation.owner
        bound = self.binding
        if (str(root.pk) != bound.root_id or str(run.root_run_id) != bound.root_id
                or run.conversation_id != root.conversation_id or user.pk != bound.owner_id
                or str(run.pk) != bound.run_id):
            raise AgentDenied("scope_mismatch")
        if maintenance:
            return
        identity = root.policy.get("identity", {})
        if (not user.is_active or user.must_change_password
                or user.grant_version != bound.grant_version or user.session_version != bound.session_version
                or identity != {"owner_id": bound.owner_id, "grant_version": bound.grant_version,
                                "session_version": bound.session_version}
                or root.policy.get("fence") != bound.fence):
            raise AgentDenied("authorization_changed")
        if root.state in TERMINAL or run.state in TERMINAL:
            raise AgentDenied("root_stopped")
        if "skill_digest" in root.policy:
            from .agent_skills import skill_bundle
            if skill_bundle(bound.owner_id)["digest"] != root.policy["skill_digest"]:
                raise AgentDenied("skill_authorization_changed")
        if not root.deadline_at or timezone.now() >= root.deadline_at:
            raise AgentDenied("deadline")
        if run.requirement_id:
            if (not run.work_id or run.requirement.work_id != run.work_id
                    or run.work.owner_id != bound.owner_id or run.work.conversation_id != root.conversation_id
                    or (run.work.state in TERMINAL and not (run.pk == root.pk and run.work.state == "completed"))
                    or run.work.current_requirement_version != run.requirement.version):
                raise AgentDenied("requirement_changed")
        from .agent_source_permissions import check_sources
        check_sources(root)

    def _load(self, locked=False):
        AgentRun, _, _ = _models()
        query = AgentRun.objects.select_for_update() if locked else AgentRun.objects
        root = query.select_related("conversation__owner").get(pk=self.binding.root_id, parent__isnull=True)
        run = root if self.binding.run_id == self.binding.root_id else AgentRun.objects.select_related(
            "work", "requirement").get(pk=self.binding.run_id)
        return root, run

    def check(self, *, maintenance=False, write=False):
        root, run = self._load()
        try:
            self._authorize(root, run, maintenance=maintenance)
        except AgentDenied as error:
            if str(error) == "requirement_changed" and run.parent_id:
                AgentRun, _, _ = _models()
                AgentRun.objects.filter(pk=run.pk).exclude(state__in=TERMINAL).update(
                    state="stopping", stop_reason="requirement_changed")
            elif str(error) in {"deadline", "authorization_changed", "requirement_changed",
                                "skill_authorization_changed", "source_authorization_changed"}:
                self.stop(str(error))
            raise
        if write and (not run.work_id or not run.requirement_id):
            raise AgentDenied("work_requirement_required")
        if write and run.work.state in TERMINAL:
            raise AgentDenied("work_not_writable")
        return run

    def _sync_work_state(self, root, run, state, reason):
        if (not root.work_id or not root.requirement_id or run.work_id != root.work_id
                or run.requirement_id != root.requirement_id):
            return
        from django.db.models import Exists, OuterRef
        from .agent_models import AgentRequirement, AgentWorkTask

        terminal = {"completed", "failed", "cancelled", "terminated"}
        active = [value for value, _ in AgentWorkTask.STATES if value not in terminal]
        current_requirement = AgentRequirement.objects.filter(
            pk=root.requirement_id, work_id=OuterRef("pk"),
            version=OuterRef("current_requirement_version"))
        AgentWorkTask.objects.filter(pk=root.work_id, owner_id=self.binding.owner_id,
            conversation_id=root.conversation_id, state__in=active).filter(
                Exists(current_requirement)).update(state=state, stop_reason=reason,
                    updated_at=timezone.now())

    def admit(self, kind, action_key=None, *, dispatch=None):
        AgentRun, AgentRootAction, _ = _models()
        action_key = action_key or str(uuid.uuid4())
        if kind not in {"model", "tool", "launch", "domain"} or not isinstance(action_key, str) or not 1 <= len(action_key) <= 160:
            raise AgentDenied("invalid_action")
        connection = transaction.get_connection()
        if connection.in_atomic_block or not connection.get_autocommit():
            raise AgentDenied("admission_requires_committed_boundary")
        self.check()
        reason = None
        with transaction.atomic(durable=True):
            root, run = self._load(locked=True)
            self._authorize(root, run)
            if kind == "domain" and (not run.work_id or not run.requirement_id):
                raise AgentDenied("work_requirement_required")
            if kind == "domain" and run.work.state in TERMINAL:
                raise AgentDenied("work_not_writable")
            policy = copy.deepcopy(root.policy)
            if dispatch is not None:
                if kind != "launch" or run.pk != root.pk:
                    raise AgentDenied("root_dispatch_required")
                existing = policy.get("dispatches", {}).get(action_key)
                if existing:
                    if existing["request"] != dispatch:
                        raise AgentDenied("operation_conflict")
                    return copy.deepcopy(existing)
                if dispatch["mode"] == "child" and run.work_id and run.work.state in TERMINAL:
                    raise AgentDenied("work_not_writable")
                if dispatch.get("notification"):
                    from .agent_models import AgentEvent
                    event = AgentEvent.objects.select_related("run").get(root=root,
                        event_key=dispatch["notification"])
                    if event.run.work_id != root.work_id or event.run.requirement_id != root.requirement_id:
                        raise AgentDenied("notification_superseded")
                target = dispatch["target"]
                if any(item["request"]["target"] == target and item["state"] in {"prepared", "sending"}
                       for item in policy.get("dispatches", {}).values()):
                    raise AgentDenied("dispatch_unknown")
                if dispatch["mode"] == "start" and root.native_thread_id:
                    raise AgentDenied("root_already_dispatched")
                if dispatch["mode"] in {"resume", "notify"} and not root.native_thread_id:
                    raise AgentDenied("root_not_resumable")
            if AgentRootAction.objects.filter(root=root, action_key=action_key).exists():
                raise AgentDenied("action_already_reserved")
            counter, limit = {"model": ("model_count", "max_model_calls"),
                              "tool": ("tool_count", "max_tool_calls"),
                              "launch": ("launch_count", "max_launches"),
                              "domain": ("launch_count", "max_launches")}[kind]
            active = policy.get("active")
            now = timezone.now().timestamp()
            if (any(type(policy.get(key)) is not int or policy[key] <= 0 for key in LIMITS)
                    or not isinstance(active, dict) or type(policy.get("active_ms")) is not int):
                reason = "invalid_policy"
            elif root.action_count >= policy["max_actions"] or getattr(root, counter) >= policy[limit]:
                reason = limit
            elif policy["active_ms"] + sum(max(0, int((now - item["started"]) * 1000)) for item in active.values()) >= policy["max_active_ms"]:
                reason = "max_active_ms"
            elif len(active) >= policy["max_concurrent"]:
                raise AgentDenied("concurrency_full")
            if reason:
                root.state = "terminated"
                root.stop_reason = reason
                policy["fence"] = policy.get("fence", 0) + 1
                root.policy = policy
                root.save(update_fields=["state", "stop_reason", "policy"])
                self._sync_work_state(root, run, "terminated", reason)
            else:
                active[action_key] = {"started": now, "kind": kind, "run_id": self.binding.run_id}
                AgentRootAction.objects.create(root=root, action_key=action_key,
                                               kind="launch" if kind == "domain" else kind)
                root.action_count += 1
                setattr(root, counter, getattr(root, counter) + 1)
                if dispatch is not None:
                    if dispatch["mode"] == "child":
                        child_id = uuid.uuid4()
                        child = AgentRun.objects.create(id=child_id, root_run_id=root.pk, parent=root,
                            conversation_id=root.conversation_id, work_id=root.work_id,
                            requirement_id=root.requirement_id, deadline_at=root.deadline_at,
                            native_thread_id=str(child_id),
                            policy={"graph_id": dispatch["graph"], "launch_action": action_key}, state="pending")
                    elif dispatch["mode"] == "update":
                        child = AgentRun.objects.get(pk=dispatch["target"], parent=root, root_run_id=root.pk)
                    else:
                        child = root
                        root.native_thread_id = str(root.pk)
                        policy["graph_id"] = dispatch["graph"]
                    intent = {"request": dispatch, "thread_id": child.native_thread_id,
                              "run_id": "", "state": "prepared"}
                    policy.setdefault("dispatches", {})[action_key] = intent
                    if dispatch.get("notification"):
                        policy.setdefault("notifications", {})[dispatch["notification"]] = {"state": "sending"}
                root.policy = policy
                root.save(update_fields=["policy", "action_count", counter, "native_thread_id"])
        if reason:
            raise AgentDenied(reason)
        return copy.deepcopy(intent) if dispatch is not None else action_key

    def finish(self, action_key, status="finished"):
        _, AgentRootAction, _ = _models()
        if status not in {"finished", "error", "cancelled"}:
            raise AgentDenied("invalid_completion")
        with transaction.atomic():
            root, run = self._load(locked=True)
            self._authorize(root, run, maintenance=True)
            policy = copy.deepcopy(root.policy)
            active = policy.get("active", {})
            item = active.get(action_key)
            if item is None:
                return
            if item["run_id"] != self.binding.run_id:
                raise AgentDenied("action_scope_mismatch")
            active.pop(action_key)
            policy["active_ms"] += max(0, int((timezone.now().timestamp() - item["started"]) * 1000))
            root.policy = policy
            root.save(update_fields=["policy"])
            AgentRootAction.objects.filter(root=root, action_key=action_key).update(status=status)

    def stop(self, reason="cancelled"):
        with transaction.atomic():
            root, run = self._load(locked=True)
            self._authorize(root, run, maintenance=True)
            if root.state not in TERMINAL:
                root.state = "stopping"
                root.stop_reason = reason
                root.policy = {**root.policy, "fence": root.policy.get("fence", 0) + 1}
                root.save(update_fields=["state", "stop_reason", "policy"])
                self._sync_work_state(root, run, "stopping", reason)
            elif root.state == "stopping":
                self._sync_work_state(root, run, "stopping", root.stop_reason or reason)

    def consume_notification(self, key):
        from .agent_models import AgentEvent
        with transaction.atomic():
            root, run = self._load(locked=True)
            self._authorize(root, run)
            notice = root.policy.get("notifications", {}).get(key)
            if not notice or run.pk != root.pk:
                raise AgentDenied("invalid_notification")
            event = AgentEvent.objects.select_related("run").get(root=root, event_key=key)
            if event.run.work_id != root.work_id or event.run.requirement_id != root.requirement_id:
                raise AgentDenied("notification_superseded")
            notice["state"] = "consumed"
            root.save(update_fields=["policy"])

    def child(self, thread_id, *, maintenance=False):
        self.check(maintenance=maintenance)
        AgentRun, _, _ = _models()
        child = AgentRun.objects.filter(root_run_id=self.binding.root_id,
            parent_id=self.binding.run_id, native_thread_id=thread_id).first()
        if child is None:
            raise AgentDenied("unknown_child")
        return child


class NativeRuntime:
    """No executor: all execution, interruption and checkpoints belong to Agent Server."""

    def __init__(self, guard, client, graphs):
        self.guard, self.client, self.graphs = guard, client, frozenset(graphs)

    async def start(self, message, operation_key, *, graph_id="main"):
        root = await sync_to_async(self.guard.check)()
        existing = root.policy.get("dispatches", {}).get(operation_key)
        mode = existing["request"]["mode"] if existing else "start"
        if mode not in {"start", "resume"}:
            raise AgentDenied("operation_conflict")
        return await self._dispatch(message, operation_key, graph_id, mode, self.guard.binding.root_id)

    async def resume(self, message, operation_key):
        root = await sync_to_async(self.guard.check)()
        existing = root.policy.get("dispatches", {}).get(operation_key)
        mode = existing["request"]["mode"] if existing else "resume"
        if mode not in {"start", "resume"}:
            raise AgentDenied("operation_conflict")
        return await self._dispatch(message, operation_key, root.policy.get("graph_id", "main"), mode, str(root.pk))

    async def launch(self, graph_id, description, operation_key):
        if graph_id not in self.graphs:
            raise AgentDenied("graph_not_allowed")
        parent = await sync_to_async(self.guard.check)()
        if parent.parent_id:
            raise AgentDenied("one_level_only")
        return await self._dispatch(description, operation_key, graph_id, "child", "child:" + operation_key)

    async def _dispatch(self, message, operation_key, graph_id, mode, target, *, notification=None):
        if self.guard.binding.run_id != self.guard.binding.root_id:
            raise AgentDenied("root_required")
        if message is None:
            payload = None
        elif isinstance(message, str):
            payload = {"messages": [{"role": "user", "content": message}]}
        elif isinstance(message, list) and message and all(isinstance(item, dict) for item in message):
            payload = {"messages": message}
        else:
            raise AgentDenied("invalid_messages")
        message_id = None
        if mode in {"start", "resume"} and operation_key.startswith("message:"):
            from .agent_models import AgentMessage
            root = await sync_to_async(self.guard.check)()
            message_id = operation_key.removeprefix("message:")
            try:
                stored = await sync_to_async(AgentMessage.objects.get)(pk=message_id,
                    conversation_id=root.conversation_id, role="user")
            except (AgentMessage.DoesNotExist, ValueError):
                raise AgentDenied("message_binding_mismatch") from None
            if not isinstance(message, str) or message != stored.content:
                raise AgentDenied("message_binding_mismatch")
        elif notification:
            root = await sync_to_async(self.guard.check)()
            message_id = root.policy.get("current_message_id")
        request = {"graph": graph_id, "mode": mode, "target": target,
                   "digest": hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                   "notification": notification, "message_id": message_id}
        intent = await sync_to_async(self.guard.admit)("launch", operation_key, dispatch=request)
        if intent["state"] == "sending":
            await self.recover_dispatches()
            root = await sync_to_async(self.guard.check)()
            intent = root.policy["dispatches"][operation_key]
        if intent["state"] == "sending":
            raise AgentDenied("dispatch_unknown")
        if intent["state"] == "aborted":
            raise AgentDenied("dispatch_aborted")
        if intent["state"] == "prepared":
            await self.client.threads.create(thread_id=intent["thread_id"], if_exists="do_nothing",
                metadata={"root_id": self.guard.binding.root_id})
            binding = RunBinding(**{**self.guard.binding.__dict__, "run_id": intent["thread_id"]})
            await sync_to_async(RuntimeGuard(binding).check)()
            def claim():
                AgentRun, _, _ = _models()
                with transaction.atomic():
                    root, run = self.guard._load(locked=True)
                    self.guard._authorize(root, run)
                    record = root.policy["dispatches"][operation_key]
                    if record["state"] != "prepared":
                        raise AgentDenied("dispatch_in_progress")
                    record["state"] = "sending"
                    if message_id and mode in {"start", "resume"}:
                        root.policy["current_message_id"] = message_id
                    root.save(update_fields=["policy"])
                    child = AgentRun.objects.get(pk=record["thread_id"])
                    child.state = "dispatch_unknown"
                    child.policy = {**child.policy, "dispatch_key": operation_key}
                    child.save(update_fields=["state", "policy"])
            await sync_to_async(claim)()
            result = await self.client.runs.create(intent["thread_id"], graph_id, input=payload,
                context={"binding": binding.token(), "operation_key": operation_key, "message_id": message_id,
                         **({"notification_key": notification} if notification else {})},
                metadata={"operation_key": operation_key, "root_id": binding.root_id},
                multitask_strategy="enqueue" if notification else "interrupt", langsmith_tracing=False,
                config={"recursion_limit": 100})
            intent = await sync_to_async(self._accepted)(operation_key, result["run_id"])
        if mode != "child":
            await sync_to_async(self.guard.finish)(operation_key)
        return {"task_id": intent["thread_id"], "thread_id": intent["thread_id"],
                "run_id": intent["run_id"], "agent_name": graph_id, "status": "submitted"}

    def _accepted(self, operation_key, native_run_id):
        AgentRun, _, _ = _models()
        with transaction.atomic():
            root, run = self.guard._load(locked=True)
            self.guard._authorize(root, run, maintenance=True)
            record = root.policy["dispatches"][operation_key]
            if record["run_id"] and record["run_id"] != native_run_id:
                raise AgentDenied("ambiguous_native_dispatch")
            record.update(state="accepted", run_id=native_run_id)
            notification = record["request"].get("notification")
            if notification:
                notice = root.policy["notifications"][notification]
                notice["run_id"] = native_run_id
                if notice["state"] != "consumed":
                    notice["state"] = "delivered"
            root.save(update_fields=["policy"])
            child = AgentRun.objects.get(pk=record["thread_id"])
            if child.policy.get("dispatch_key") == operation_key:
                child.native_run_id = native_run_id
                if child.state not in TERMINAL:
                    child.state = "running"
                child.save(update_fields=["native_run_id", "state"])
            return copy.deepcopy(record)

    async def check(self, task_id, *, maintenance=False):
        child = await sync_to_async(self.guard.child)(task_id, maintenance=maintenance)
        if not child.native_run_id:
            return {"task_id": task_id, "status": "dispatch_unknown"}
        result = await self.client.runs.get(task_id, child.native_run_id)
        output = {"task_id": task_id, "run_id": child.native_run_id, "status": result["status"]}
        if result["status"] == "success" and not maintenance:
            state = await self.client.threads.get_state(task_id)
            await sync_to_async(self.guard.check)()
            messages = state.get("values", {}).get("messages", [])
            output["result"] = messages[-1].get("content", "") if messages else ""
        return output

    async def update(self, task_id, message, operation_key):
        child = await sync_to_async(self.guard.child)(task_id)
        if child.state in TERMINAL:
            raise AgentDenied("child_not_updatable")
        return await self._dispatch(message, operation_key, child.policy["graph_id"], "update", str(child.pk))

    async def cancel(self, task_id):
        child = await sync_to_async(self.guard.child)(task_id, maintenance=True)
        unresolved, known = await sync_to_async(self._freeze_dispatches)(task_id)
        for run_id in known:
            await self._cancel_run(task_id, run_id)
        if unresolved:
            return {"task_id": task_id, "status": "dispatch_unknown"}
        child.state = "cancelled"
        await sync_to_async(child.save)(update_fields=["state"])
        await sync_to_async(self.guard.finish)(child.policy["launch_action"], "cancelled")
        return {"task_id": task_id, "status": "cancelled"}

    def _freeze_dispatches(self, thread_id):
        AgentRun, _, _ = _models()
        aborted = []
        with transaction.atomic():
            root, run = self.guard._load(locked=True)
            self.guard._authorize(root, run, maintenance=True)
            target = AgentRun.objects.get(root_run_id=root.pk, native_thread_id=thread_id)
            if str(target.pk) != self.guard.binding.root_id:
                target.state = "stopping"
                target.save(update_fields=["state"])
            known = {target.native_run_id} if target.native_run_id else set()
            unresolved = False
            for operation, intent in root.policy.get("dispatches", {}).items():
                if intent["thread_id"] != thread_id:
                    continue
                if intent["state"] in {"prepared", "aborted"}:
                    intent["state"] = "aborted"
                    aborted.append(operation)
                elif intent["state"] == "sending":
                    unresolved = True
                elif intent.get("run_id"):
                    known.add(intent["run_id"])
            root.save(update_fields=["policy"])
        for operation in aborted:
            self.guard.finish(operation, "cancelled")
        return unresolved, sorted(known)

    async def _cancel_run(self, thread_id, run_id):
        import httpx
        root, _ = await sync_to_async(self.guard._load)()
        if run_id in root.policy.get("native_cancelled", []):
            return
        try:
            await self.client.runs.cancel(thread_id, run_id, wait=True, action="interrupt")
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 404:
                raise
        def acknowledged():
            with transaction.atomic():
                fresh, run = self.guard._load(locked=True)
                self.guard._authorize(fresh, run, maintenance=True)
                cancelled = fresh.policy.setdefault("native_cancelled", [])
                if run_id not in cancelled:
                    cancelled.append(run_id)
                fresh.save(update_fields=["policy"])
        await sync_to_async(acknowledged)()

    async def list(self, *, maintenance=False):
        await sync_to_async(self.guard.check)(maintenance=maintenance)
        AgentRun, _, _ = _models()
        children = await sync_to_async(list)(AgentRun.objects.filter(root_run_id=self.guard.binding.root_id,
            parent_id=self.guard.binding.run_id).values_list("native_thread_id", flat=True))
        return await asyncio.gather(*(self.check(task, maintenance=maintenance) for task in children))

    async def reconcile(self):
        """One bounded non-LLM pass; event keys make duplicate delivery harmless."""
        from .agent_models import append_public_event
        try:
            await sync_to_async(self.guard.check)()
        except AgentDenied:
            pass
        root, _ = await sync_to_async(self.guard._load)()
        if root.state in TERMINAL:
            AgentRun, _, _ = _models()
            children = await sync_to_async(list)(AgentRun.objects.filter(root_run_id=root.pk,
                parent_id=self.guard.binding.run_id).exclude(state="cancelled"))
            results = []
            if root.native_thread_id:
                unresolved, known = await sync_to_async(self._freeze_dispatches)(root.native_thread_id)
                for run_id in known:
                    await self._cancel_run(root.native_thread_id, run_id)
                if unresolved:
                    results.append({"task_id": root.native_thread_id, "status": "dispatch_unknown"})
            for child in children:
                results.append(await self.cancel(child.native_thread_id))
            return results
        await self.recover_dispatches()
        statuses = await self.list(maintenance=True)
        root, _ = await sync_to_async(self.guard._load)()
        for result in statuses:
            child = await sync_to_async(self.guard.child)(result["task_id"], maintenance=True)
            if root.state in TERMINAL and result["status"] in {"pending", "running"}:
                await self.cancel(result["task_id"])
                continue
            if result["status"] not in {"success", "error", "timeout", "interrupted"}:
                continue
            await sync_to_async(self.guard.finish)(child.policy["launch_action"])
            historical = child.work_id != root.work_id or child.requirement_id != root.requirement_id
            event, _ = await sync_to_async(append_public_event)(root.pk, child.pk,
                f"native:{child.native_run_id}:{result['status']}",
                "child_terminal_historical" if historical else "child_terminal", result)
            if not historical and root.state not in TERMINAL and root.native_thread_id:
                await self._wake(event, child)
        return statuses

    async def recover_dispatches(self):
        await sync_to_async(self.guard.check)(maintenance=True)
        root, _ = await sync_to_async(self.guard._load)()
        bound = root.policy.get("max_actions", 0)
        if type(bound) is not int or bound <= 0:
            raise AgentDenied("invalid_policy")
        for operation, intent in root.policy.get("dispatches", {}).items():
            if intent["state"] != "sending":
                continue
            matches = []
            for offset in range(0, bound + 100, 100):
                records = await self.client.runs.list(intent["thread_id"], limit=100, offset=offset)
                matches.extend(record for record in records if record.get("metadata", {}).get("operation_key") == operation
                    and record.get("metadata", {}).get("root_id", self.guard.binding.root_id) == self.guard.binding.root_id)
                if len(records) < 100:
                    break
            if len(matches) == 1:
                await sync_to_async(self._accepted)(operation, matches[0]["run_id"])
                if intent["request"]["mode"] != "child":
                    await sync_to_async(self.guard.finish)(operation)
            elif len(matches) > 1:
                raise AgentDenied("ambiguous_native_dispatch")

    async def _wake(self, event, child):
        root = await sync_to_async(self.guard.check)()
        if child.work_id != root.work_id or child.requirement_id != root.requirement_id:
            return
        notice = root.policy.get("notifications", {}).get(event.event_key)
        if notice and notice["state"] in {"delivered", "consumed"}:
            return
        state = await self.client.threads.get_state(child.native_thread_id)
        await sync_to_async(self.guard.check)()
        messages = state.get("values", {}).get("messages", [])
        final = messages[-1].get("content", "") if messages else ""
        root = await sync_to_async(self.guard.check)()
        await self._dispatch([{"role": "user", "id": str(event.pk), "content":
            "Authorized child completion: " + str(child.pk) + "\n" + str(final)}],
            "notify:" + str(event.pk), root.policy["graph_id"], "notify", str(root.pk), notification=event.event_key)


def _reconcile_binding(root):
    identity = root.policy.get("identity", {})
    return RunBinding(identity.get("owner_id"), str(root.pk), str(root.pk),
        identity.get("grant_version"), identity.get("session_version"), root.policy.get("fence"))


async def reconcile_deployment_once(after=None):
    AgentRun, _, _ = _models()
    query = AgentRun.objects.filter(parent__isnull=True).exclude(native_thread_id="")
    if after:
        query = query.filter(pk__gt=after)
    roots = await sync_to_async(list)(query.order_by("pk")[:32])
    for root in roots:
        try:
            binding = _reconcile_binding(root)
            async with same_deployment_client(binding) as client:
                await NativeRuntime(RuntimeGuard(binding), client, {"researcher", "reviewer"}).reconcile()
        except Exception as error:
            logging.getLogger(__name__).warning("Native reconciliation deferred: root=%s error=%s",
                                               root.pk, type(error).__name__)
    return roots[-1].pk if len(roots) == 32 else None


@asynccontextmanager
async def runtime_lifespan(app):
    await sync_to_async(setup_runtime_django)()
    from django.conf import settings
    async def reconcile_loop():
        cursor = None
        while True:
            await asyncio.sleep(2)
            try:
                cursor = await reconcile_deployment_once(cursor)
            except Exception as error:
                logging.getLogger(__name__).warning("Native reconciliation unavailable: %s", type(error).__name__)
    task = asyncio.create_task(reconcile_loop()) if getattr(settings, "AGENT_PLATFORM_ENABLED", False) else None
    try:
        yield
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


def same_deployment_client(binding):
    import httpx
    from django.conf import settings
    from langgraph_sdk.client import LangGraphClient
    from langgraph_api.server import app
    return LangGraphClient(httpx.AsyncClient(base_url="http://agent-runtime",
        transport=httpx.ASGITransport(app=app), timeout=15, trust_env=False,
        headers={"Authorization": "Bearer " + settings.AGENT_RUNTIME_SERVICE_TOKEN,
                 "X-Agent-Binding": binding.token()}))


async def reconcile_request(request):
    from django.conf import settings
    from starlette.responses import JSONResponse
    expected = getattr(settings, "AGENT_RUNTIME_SERVICE_TOKEN", "")
    if len(expected) < 40 or not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + expected):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        binding = RunBinding.from_token(request.headers.get("x-agent-binding", ""))
        guard = RuntimeGuard(binding)
        await sync_to_async(guard.check)(maintenance=True)
        async with same_deployment_client(binding) as client:
            results = await NativeRuntime(guard, client, {"researcher", "reviewer"}).reconcile()
        return JSONResponse({"runs": results})
    except AgentDenied:
        return JSONResponse({"error": "invalid_scope"}, status_code=403)


from starlette.applications import Starlette
from starlette.routing import Route

runtime_app = Starlette(lifespan=runtime_lifespan,
    routes=[Route("/agent/reconcile", reconcile_request, methods=["POST"])])
