from functools import wraps
from types import SimpleNamespace

from django.db import transaction
from django.utils import timezone

from .agent_models import (AgentBusinessReference, AgentMessage, AgentRequirement, AgentRootAction, AgentRun,
                           AgentWorkTask, append_public_event)
from .agent_runtime import AgentDenied, RuntimeGuard
from .agent_db import database_boundary
from .models import User
from .product_agent import check_task_scope, lock_scope
from .product_models import DocumentArtifact, DocumentRevision, DocumentTask
from .product_service import (ProductError, append_revision, approved_blueprint, digest,
                              input_authorized, require_version, task_for, validate_input)


def domain_side_effect(domain, name):
    def decorate(method):
        @wraps(method)
        def wrapped(self, *args, **kwargs):
            operation_key = kwargs.get("operation_key", args[-1] if args else None)
            self._reserve(name, operation_key)
            self.guard.check(write=True)
            reference_key = f"{domain}:{name}:{operation_key}"
            action_key = f"domain:{self.guard.binding.run_id}:{digest(reference_key)[:32]}"
            previous = AgentRootAction.objects.filter(root_id=self.root_run_id, action_key=action_key).first()
            if previous:
                if not AgentBusinessReference.objects.filter(root_id=self.root_run_id,
                                                             operation_key=reference_key).exists():
                    raise ProductError("operation_unknown", "领域动作结果待核对。", 409)
                if previous.status != "finished":
                    self.guard.finish(action_key)
                return method(self, *args, **kwargs)
            admitted = self.guard.admit("domain", action_key)
            try:
                result = method(self, *args, **kwargs)
            except Exception:
                self.guard.finish(admitted, "error")
                raise
            self.guard.finish(admitted)
            return result
        return wrapped
    return decorate


class AgentTools:
    def __init__(self, guard: RuntimeGuard, *, message_id=None):
        if not isinstance(guard, RuntimeGuard):
            raise AgentDenied("runtime_guard_required")
        run = guard.check()
        self.guard = guard
        self.actor_id = guard.binding.owner_id
        self.root_run_id = guard.binding.root_id
        self.message_id = message_id
        if run.conversation.owner_id != self.actor_id:
            raise AgentDenied("scope_mismatch")

    def _actor(self):
        return User.objects.get(pk=self.actor_id)

    def _read_source_root(self):
        from .agent_models import AgentRun
        return AgentRun.objects.get(pk=self.root_run_id, parent__isnull=True)

    def _manager_actor(self):
        if self._department() != "":
            raise ProductError("agent_department_unavailable", "经营引用不可访问。", 403)
        from .agent_management import _manager
        actor = self._actor()
        try:
            _manager(SimpleNamespace(agent_user=actor))
        except Exception:
            raise ProductError("agent_authorization_changed", "总经理只读授权已失效。", 403) from None
        return actor

    def _department(self):
        from .agent_api import AgentApiError, actor, department
        try:
            user = actor(SimpleNamespace(agent_user=self._actor()))
        except AgentApiError:
            raise ProductError("agent_authorization_changed", "当前部门授权已失效。", 403) from None
        return department(user)

    def _scope(self):
        run = self.guard.check(write=True)
        return lock_scope(self.root_run_id, run.work_id, self._actor(), run.requirement.version,
                          self.guard.binding.grant_version, self.guard.binding.session_version,
                          self.guard.binding.fence)

    def _department_scope(self, department):
        if self._department() != department:
            raise ProductError("agent_department_unavailable", "当前部门业务工具不可用。", 403)
        run = self.guard.check(write=True)
        return lock_scope(self.root_run_id, run.work_id, self._actor(), run.requirement.version,
                          self.guard.binding.grant_version, self.guard.binding.session_version,
                          self.guard.binding.fence, department=department)

    def _reserve(self, name, operation_key):
        if (not isinstance(operation_key, str) or not operation_key or len(operation_key) > 80
                or any(ord(character) < 33 for character in operation_key)):
            raise ProductError("invalid_operation_key", "工具操作键无效。")
        self.guard.check()

    def _product_read_scope(self):
        if self._department() != "product" or self.guard.check().conversation.department_code != "product":
            raise ProductError("agent_department_unavailable", "当前部门业务工具不可用。", 403)

    def _product_input_sources(self, task, hashes):
        from .agent_read_sources import read_digest, register_read_sources

        hashes = set(hashes)
        if not hashes:
            return
        revisions = list(task.revisions.filter(kind=DocumentRevision.Kind.INPUT, sha256__in=hashes))
        if {revision.sha256 for revision in revisions} != hashes:
            raise ProductError("source_permission_changed", "资料授权已变化，当前内容不可读取。", 404)
        sources = []
        for revision in revisions:
            if read_digest(revision.payload) != revision.sha256 or not input_authorized(task, revision):
                raise ProductError("source_permission_changed", "资料授权已变化，当前内容不可读取。", 404)
            sources.append({"kind": "product_task", "task_id": str(task.pk), "owner_id": task.owner_id,
                            "task_version": task.version, "input_revision_id": str(revision.pk),
                            "input_version": revision.version, "input_sha256": revision.sha256})
        register_read_sources(self._read_source_root(), sources)

    def _product_source_pointer(self, task, source):
        from .agent_read_sources import read_digest
        from .product_storage import verified_artifact

        revision = task.revisions.filter(kind=DocumentRevision.Kind.INPUT, version=task.input_version).first()
        if task.input_version and revision is None:
            raise ProductError("source_permission_changed", "资料授权已变化，当前内容不可读取。", 404)
        if revision is not None:
            self._product_input_sources(task, {revision.sha256})
        verified_artifact(source)
        return {"kind": "product_source", "task_id": str(task.pk), "owner_id": task.owner_id,
                "task_version": task.version, "input_revision_id": str(revision.pk) if revision else "",
                "input_version": revision.version if revision else 0,
                "input_sha256": revision.sha256 if revision else "", "source_id": str(source.pk),
                "uploaded_by_id": source.uploaded_by_id, "sha256": source.sha256,
                "parsed_sha256": read_digest(source.parsed)}

    def _product_artifact_pointer(self, task, artifact, revision):
        from .product_storage import verified_artifact

        if revision is None or artifact.input_hash != revision.sha256 or not input_authorized(task, revision):
            raise ProductError("source_permission_changed", "资料授权已变化，当前内容不可读取。", 404)
        verified_artifact(artifact)
        return {"kind": "product_artifact", "task_id": str(task.pk), "owner_id": task.owner_id,
                "task_version": task.version, "input_revision_id": str(revision.pk),
                "input_version": revision.version, "input_sha256": revision.sha256,
                "artifact_id": str(artifact.pk), "version": artifact.version,
                "sha256": artifact.sha256, "input_hash": artifact.input_hash}

    @transaction.atomic
    def begin_work(self, operation_key):
        self._reserve("begin_work", operation_key)
        if not self.message_id or self.guard.binding.run_id != self.root_run_id:
            raise ProductError("agent_message_required", "当前消息未绑定可信运行。", 409)
        root = AgentRun.objects.select_for_update().select_related("conversation").get(pk=self.root_run_id)
        message = AgentMessage.objects.select_for_update().filter(
            pk=self.message_id, conversation=root.conversation, role="user").first()
        if message is None:
            raise ProductError("agent_message_required", "当前消息不可用于建立工作。", 409)
        current_work = AgentWorkTask.objects.select_for_update().get(pk=root.work_id) if root.work_id else None
        if message.work_id:
            linked = AgentRequirement.objects.filter(work_id=message.work_id, user_message=message).first()
            if (linked is None or message.work.owner_id != self.actor_id
                    or message.work.conversation_id != root.conversation_id):
                raise ProductError("agent_message_required", "当前消息不可用于建立工作。", 409)
            if current_work and message.work_id == current_work.pk:
                return {"work_id": str(current_work.pk), "requirement_version": root.requirement.version}
            if message.work.state == "completed":
                return {"work_id": str(message.work_id), "requirement_version": linked.version}
            raise ProductError("agent_work_exists", "此消息已关联其他工作。", 409)
        if not message.content.strip():
            raise ProductError("agent_message_required", "当前消息不可用于建立工作。", 409)
        if current_work and current_work.state != "completed":
            raise ProductError("agent_work_exists", "此根运行已有工作。", 409)
        actor = self._actor()
        department = self._department()
        if department not in {"product", "hr", "finance"} or root.conversation.department_code != department:
            raise ProductError("agent_department_unavailable", "当前部门业务工具尚未接入。", 403)
        work = AgentWorkTask.objects.create(
            owner=actor, department_code=department, conversation=root.conversation,
            project=root.conversation.project, goal=message.content, public_summary=message.content[:300],
            state="running", current_requirement_version=1,
        )
        requirement = AgentRequirement.objects.create(
            work=work, version=1, user_message=message, content=message.content, applied_at=timezone.now())
        message.work = work
        message.save(update_fields=["work"])
        root.work = work
        root.requirement = requirement
        root.save(update_fields=["work", "requirement", "updated_at"])
        append_public_event(root.pk, root.pk, f"begin_work:{message.pk}", "work_started",
                            {"work_id": str(work.pk), "requirement_version": 1}, work=work)
        return {"work_id": str(work.pk), "requirement_version": 1}

    def _reference(self, root, name, operation_key, arguments):
        key = f"product:{name}:{operation_key}"
        existing = AgentBusinessReference.objects.filter(root=root, operation_key=key).first()
        if existing:
            if existing.digest != digest(arguments):
                raise ProductError("idempotency_conflict", "操作键已用于不同内容。", 409)
            return existing
        return None

    def _save_reference(self, root, work, name, operation_key, arguments, task):
        AgentBusinessReference.objects.create(
            root=root, work=work, requirement=root.requirement, domain_type="document_task",
            object_id=str(task.pk), revision=str(task.version), digest=digest(arguments),
            operation_key=f"product:{name}:{operation_key}", public_summary=task.title,
        )

    @domain_side_effect("product", "create")
    @transaction.atomic
    def create_product_task(self, title, operation_key):
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ProductError("invalid_title", "项目名称无效。")
        root, work = self._scope()
        arguments = {"title": title.strip()}
        self._reserve("create", operation_key)
        existing = self._reference(root, "create", operation_key, arguments)
        if existing:
            task = task_for(self._actor(), existing.object_id)
            check_task_scope(task, (root, work))
            return {"task_id": str(task.pk), "state": task.state, "version": task.version,
                    "next": "upload_equipment_and_background_sources"}
        actor = self._actor()
        key = digest({"root": str(root.pk), "operation": operation_key})
        input_payload = validate_input({"project": title.strip(), "requirements": work.goal, "background": "",
                                        "conditions": [], "items": []})
        task = DocumentTask.objects.create(
            owner=actor, title=title.strip(), idempotency_key=key, payload_hash=digest(arguments),
            checkpoint={"intake_mode": "equipment_background"}, agent_root_id=str(root.pk),
            agent_work_id=str(work.pk), agent_requirement_version=work.current_requirement_version,
            agent_grant_version=self.guard.binding.grant_version,
            agent_session_version=self.guard.binding.session_version,
            agent_root_fence=self.guard.binding.fence,
        )
        revision = append_revision(task, DocumentRevision.Kind.INPUT, input_payload, actor=actor, reason="agent_task_created")
        task.input_version = revision.version
        task.save(update_fields=["input_version", "updated_at"])
        self._save_reference(root, work, "create", operation_key, arguments, task)
        return {"task_id": str(task.pk), "state": task.state, "version": task.version,
                "next": "upload_equipment_and_background_sources"}

    @domain_side_effect("product", "blueprint")
    @transaction.atomic
    def queue_product_blueprint(self, task_id, expected_version, operation_key):
        from .product_api import _queue
        root, work = self._scope()
        arguments = {"task_id": task_id, "expected_version": expected_version}
        self._reserve("blueprint", operation_key)
        existing = self._reference(root, "blueprint", operation_key, arguments)
        if existing:
            task = task_for(self._actor(), existing.object_id)
            check_task_scope(task, (root, work))
            return {"task_id": str(task.pk), "state": task.state, "version": task.version,
                    "blocked": task.state == "WAITING_INPUT", "error_code": task.error_code}
        task = task_for(self._actor(), task_id, write=True)
        check_task_scope(task, (root, work))
        require_version(task, expected_version)
        blocked = _queue(task, "start")
        self._save_reference(root, work, "blueprint", operation_key, arguments, task)
        return {"task_id": str(task.pk), "state": task.state, "version": task.version,
                "blocked": blocked, "error_code": task.error_code}

    @domain_side_effect("product", "outputs")
    @transaction.atomic
    def queue_product_outputs(self, task_id, expected_version, operation_key):
        from .product_api import _queue
        from .product_agent import record_product_sources
        root, work = self._scope()
        arguments = {"task_id": task_id, "expected_version": expected_version}
        self._reserve("outputs", operation_key)
        existing = self._reference(root, "outputs", operation_key, arguments)
        if existing:
            task = task_for(self._actor(), existing.object_id)
            check_task_scope(task, (root, work))
            return {"task_id": str(task.pk), "state": task.state, "version": task.version,
                    "blocked": task.state == "WAITING_INPUT", "error_code": task.error_code}
        task = task_for(self._actor(), task_id, write=True)
        check_task_scope(task, (root, work))
        require_version(task, expected_version)
        if approved_blueprint(task) is None:
            raise ProductError("blueprint_approval_required", "等待任务所有者确认精确蓝图版本。", 409)
        blocked = _queue(task, "generate_outputs")
        record_product_sources(task, root, work)
        self._save_reference(root, work, "outputs", operation_key, arguments, task)
        return {"task_id": str(task.pk), "state": task.state, "version": task.version,
                "blocked": blocked, "error_code": task.error_code}

    @transaction.atomic
    def read_product_task(self, task_id, operation_key):
        from .product_api import _task_detail
        self._reserve("read", operation_key)
        self._product_read_scope()
        actor = self._actor()
        task = task_for(actor, task_id)
        if task.owner_id != actor.pk:
            raise ProductError("not_found", "对象不存在。", 404)
        detail = _task_detail(task, actor)
        hashes = set()
        if detail["input"] is not None:
            revision = task.revisions.filter(kind=DocumentRevision.Kind.INPUT,
                                             version=task.input_version).first()
            if revision is None:
                raise ProductError("source_permission_changed", "资料授权已变化，当前内容不可读取。", 404)
            hashes.add(revision.sha256)
        blueprint = task.revisions.filter(kind=DocumentRevision.Kind.BLUEPRINT,
                                          version=task.blueprint_version).first() if task.blueprint_version else None
        if blueprint:
            hashes.add(blueprint.input_hash)
        for group in (detail["chapters"], detail["reports"], detail["artifacts"]):
            hashes.update(item["input_hash"] for item in group if item.get("input_hash"))
        included_approvals = set(item["id"] for item in detail["approvals"])
        for approval in task.approvals.select_related("revision", "artifact"):
            if str(approval.pk) not in included_approvals:
                continue
            hashes.add(approval.revision.input_hash if approval.revision_id else approval.artifact.input_hash)
        self._product_input_sources(task, hashes)
        source_ids = {item["id"] for item in detail["sources"]}
        from .agent_read_sources import register_read_sources
        source_pointers = [self._product_source_pointer(task, source)
                           for source in task.sources.filter(pk__in=source_ids)]
        register_read_sources(self._read_source_root(), source_pointers)
        return detail

    @transaction.atomic
    def read_product_source(self, task_id, source_id, offset, operation_key):
        from .product_intake import source_text
        self._reserve("source", operation_key)
        self._product_read_scope()
        if type(offset) is not int or offset < 0:
            raise ProductError("invalid_offset", "资料位置无效。")
        task = task_for(self._actor(), task_id)
        if task.owner_id != self.actor_id:
            raise ProductError("not_found", "对象不存在。", 404)
        try:
            source = task.sources.get(pk=source_id)
        except (task.sources.model.DoesNotExist, ValueError, TypeError):
            raise ProductError("not_found", "对象不存在。", 404) from None
        content = source_text(source.parsed)
        from .agent_read_sources import register_read_sources
        register_read_sources(self._read_source_root(), [self._product_source_pointer(task, source)])
        return {"source_id": str(source.pk), "sha256": source.sha256, "offset": offset,
                "total_characters": len(content), "text": content[offset:offset + 20000]}

    @transaction.atomic
    def search_product_sources(self, task_id, query, operation_key):
        from .product_intake import source_text
        self._reserve("search", operation_key)
        self._product_read_scope()
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise ProductError("invalid_query", "检索词无效。")
        task = task_for(self._actor(), task_id)
        if task.owner_id != self.actor_id:
            raise ProductError("not_found", "对象不存在。", 404)
        matches = []
        sources = []
        for source in task.sources.order_by("created_at"):
            sources.append(self._product_source_pointer(task, source))
            content = source_text(source.parsed)
            position = content.casefold().find(query.casefold())
            if position >= 0:
                matches.append({"source_id": str(source.pk), "sha256": source.sha256,
                                "position": position, "excerpt": content[max(0, position - 120):position + 280]})
            if len(matches) == 8:
                break
        from .agent_read_sources import register_read_sources
        register_read_sources(self._read_source_root(), sources)
        return matches

    @transaction.atomic
    def list_product_outputs(self, task_id, operation_key):
        from .product_pair import output_current
        self._reserve("list_outputs", operation_key)
        self._product_read_scope()
        task = task_for(self._actor(), task_id)
        if task.owner_id != self.actor_id:
            raise ProductError("not_found", "对象不存在。", 404)
        outputs = []
        sources = []
        for artifact in DocumentArtifact.objects.filter(task=task).order_by("version"):
            revision = task.revisions.filter(kind="input", sha256=artifact.input_hash).first()
            if input_authorized(task, revision):
                sources.append(self._product_artifact_pointer(task, artifact, revision))
                outputs.append({"artifact_id": str(artifact.pk), "family": artifact.family,
                                "version": artifact.version, "sha256": artifact.sha256,
                                "current": output_current(task, artifact)})
        from .agent_read_sources import register_read_sources
        register_read_sources(self._read_source_root(), sources)
        return outputs

    @transaction.atomic
    def finance_read_drafts(self, operation_key):
        from .agent_read_sources import register_read_sources
        from .business_boards import _checksum, _grant, _row_checksum
        from .business_models import BusinessLedgerWorkbook
        self._reserve("finance_read", operation_key)
        if (self._department() != "finance"
                or self.guard.check().conversation.department_code != "finance"):
            raise ProductError("agent_department_unavailable", "当前部门业务工具不可用。", 403)
        actor = self._actor()
        if _grant(actor, "finance") is None:
            raise ProductError("agent_authorization_changed", "财务台账授权已失效。", 403)
        workbook = BusinessLedgerWorkbook.objects.filter(department="finance").first()
        if workbook is None:
            return {"revision": 0, "records": []}
        revision = workbook.versions.filter(revision=workbook.revision).first()
        records, pointers = [], []
        if workbook.records and revision is None:
            raise ProductError("source_authorization_changed", "财务台账历史版本不可核验。", 409)
        if revision:
            snapshot = SimpleNamespace(department=workbook.department, state=revision.state,
                                       as_of=revision.as_of, records=revision.records)
            if _checksum(snapshot, records=revision.records, state=revision.state) != revision.checksum:
                raise ProductError("source_authorization_changed", "财务台账历史版本不可核验。", 409)
        for row in workbook.records:
            meta = next((value for value in workbook.record_meta.values()
                         if value.get("project_id") == row.get("project_id")
                         and value.get("record_author_id") == actor.pk and not value.get("deleted")), None)
            if not meta:
                continue
            record_id = next(key for key, value in workbook.record_meta.items() if value is meta)
            snapshot_meta = revision.record_meta.get(record_id) if revision else None
            snapshot_rows = [item for item in (revision.records if revision else [])
                             if item.get("project_id") == row.get("project_id")]
            if (not isinstance(snapshot_meta, dict) or len(snapshot_rows) != 1
                    or snapshot_meta.get("project_id") != row.get("project_id")
                    or snapshot_meta.get("record_author_id") != actor.pk
                    or snapshot_meta.get("draft_actor_id") != actor.pk
                    or snapshot_meta.get("deleted")
                    or type(snapshot_meta.get("draft_revision")) is not int
                    or type(snapshot_meta.get("draft_checksum")) is not str
                    or _row_checksum(snapshot_rows[0]) != snapshot_meta.get("draft_checksum")):
                raise ProductError("source_authorization_changed", "财务台账本人记录不可核验。", 409)
            pointers.append({"kind": "finance_draft", "owner_id": actor.pk,
                "workbook_id": str(workbook.pk), "revision_id": str(revision.pk),
                "revision": revision.revision, "revision_checksum": revision.checksum,
                "record_id": record_id, "project_id": row["project_id"],
                "record_author_id": snapshot_meta["record_author_id"],
                "draft_actor_id": snapshot_meta["draft_actor_id"],
                "draft_revision": snapshot_meta["draft_revision"],
                "draft_checksum": snapshot_meta["draft_checksum"],
                "record_sha256": _row_checksum(snapshot_rows[0])})
            records.append({"record_id": record_id, "record": row})
        register_read_sources(self._read_source_root(), pointers)
        return {"revision": workbook.revision, "records": records}

    @domain_side_effect("finance", "draft")
    @transaction.atomic
    def finance_save_draft(self, record, expected_revision, operation_key):
        from .business_boards import (BoardError, MAX_ROWS, _editable, _finance_apply_records,
                                      _lock_scope, _save_mutation, validate_record)
        root, work = self._department_scope("finance")
        arguments = {"record": record, "expected_revision": expected_revision}
        key = f"finance:draft:{operation_key}"
        previous = AgentBusinessReference.objects.filter(root=root, operation_key=key).first()
        if previous:
            if previous.digest != digest(arguments):
                raise ProductError("idempotency_conflict", "操作键已用于不同内容。", 409)
            return {"project_id": previous.object_id, "revision": int(previous.revision)}
        if type(expected_revision) is not int or expected_revision < 0:
            raise ProductError("invalid_revision", "财务台账版本无效。")
        try:
            actor, _, workbook = _lock_scope(self._actor(), "finance", "can_edit", expected_revision)
            _editable(workbook)
            row = validate_record(record, "finance")
            records = list(workbook.records)
            index = next((index for index, existing in enumerate(records)
                          if existing["project_id"] == row["project_id"]), None)
            if index is None:
                if len(records) >= MAX_ROWS:
                    raise BoardError(f"每个台账最多 {MAX_ROWS} 条记录。")
                records.append(row)
                action = "record_create"
            else:
                records[index] = row
                action = "record_update"
            _finance_apply_records(workbook, actor, records)
            workbook.source_name = "手工录入"
            _save_mutation(workbook, actor, action)
        except PermissionError:
            raise ProductError("agent_authorization_changed", "财务编辑授权已失效。", 403) from None
        except RuntimeError:
            raise ProductError("stale_version", "台账版本已变化。", 409) from None
        except BoardError as error:
            raise ProductError("invalid_finance_record", str(error), 409) from None
        AgentBusinessReference.objects.create(
            root=root, work=work, requirement=root.requirement, domain_type="finance_record",
            object_id=row["project_id"], revision=str(workbook.revision), digest=digest(arguments),
            operation_key=key, public_summary=row["project_name"][:300])
        work.state = "waiting_confirmation"
        work.public_summary = "本人财务草稿已保存，等待本人核对精确版本并发布。"
        work.save(update_fields=["state", "public_summary", "updated_at"])
        return {"project_id": row["project_id"], "revision": workbook.revision}

    @transaction.atomic
    def hr_read_jd(self, request_id, operation_key):
        from .hr_recruitment_api import _data, _owned
        from .agent_read_sources import read_digest, register_read_sources
        self._reserve("hr_read_jd", operation_key)
        if self._department() != "hr":
            raise ProductError("agent_department_unavailable", "当前部门业务工具不可用。", 403)
        request = _owned(self._actor(), request_id)
        request_data = _data(request)
        jds = [
            {"id": str(jd.pk), "version": jd.version, "input_version": jd.input_version,
             "state": jd.state, "channel": jd.channel, "stale": jd.stale, "body": jd.body}
            for jd in request.jd_versions.order_by("version") if not jd.stale]
        sources = [{"kind": "hr_request", "request_id": str(request.pk),
                    "version": request.input_version, "body_sha256": read_digest(request_data)}]
        sources.extend({"kind": "hr_jd", "request_id": str(request.pk), "jd_id": item["id"],
                        "version": item["version"], "input_version": item["input_version"],
                        "body_sha256": read_digest(item["body"])} for item in jds)
        register_read_sources(self._read_source_root(), sources)
        return {"request": request_data, "jd_versions": jds}

    @transaction.atomic
    def hr_read_batch(self, batch_id, offset, operation_key):
        from .hr_screening_api import batch_data, owned_batch
        from .hr_screening_results import _rows
        from .agent_read_sources import read_digest, register_read_sources
        from .hr_resume_storage import read_file
        from .hr_retention import active_artifacts
        from .hr_screening_models import ResumeArtifact
        from .product_storage import StorageError
        self._reserve("hr_read_batch", operation_key)
        if self._department() != "hr":
            raise ProductError("agent_department_unavailable", "当前部门业务工具不可用。", 403)
        if type(offset) is not int or offset < 0:
            raise ProductError("invalid_offset", "分页位置无效。")
        batch = owned_batch(self._actor(), batch_id)
        rows = _rows(batch, {})
        results = rows[offset:offset + 20]
        batch_payload = batch_data(batch)
        artifacts = list(active_artifacts(ResumeArtifact.objects.filter(
            batch=batch, pk__in=[item["id"] for item in results])))
        by_id = {str(item.pk): item for item in artifacts}
        if set(by_id) != {item["id"] for item in results}:
            raise ProductError("hr_source_unavailable", "简历结果授权已变化。", 409)
        sources = [{"kind": "hr_batch", "batch_id": str(batch.pk), "version": batch.version,
                    "request_id": str(batch.jd_version.request_id),
                    "request_version": batch.jd_version.request.input_version,
                    "request_sha256": read_digest({"id": str(batch.jd_version.request_id),
                        "input_version": batch.jd_version.request.input_version,
                        "position_name": batch.jd_version.request.position_name}),
                    "jd_id": str(batch.jd_version_id), "jd_version": batch.jd_version.version,
                    "jd_body_sha256": read_digest(batch.jd_version.body),
                    "body_sha256": read_digest({"batch": batch_payload, "results": results})},
                   {"kind": "hr_jd", "request_id": str(batch.jd_version.request_id),
                    "jd_id": str(batch.jd_version_id), "version": batch.jd_version.version,
                    "input_version": batch.jd_version.input_version,
                    "body_sha256": read_digest(batch.jd_version.body)}]
        for row in results:
            item = by_id[row["id"]]
            try:
                read_file(item.file_id, item.sha256)
            except StorageError:
                raise ProductError("hr_source_unavailable", "简历文件缺失或完整性校验失败。", 409) from None
            sources.append({"kind": "hr_resume", "artifact_id": str(item.pk),
                            "batch_id": str(batch.pk), "version": item.version,
                            "sha256": item.sha256, "file_id": item.file_id})
        register_read_sources(self._read_source_root(), sources)
        return {"batch": batch_payload, "total": len(rows), "offset": offset, "results": results}

    @domain_side_effect("hr", "queue")
    @transaction.atomic
    def hr_queue_batch(self, batch_id, expected_version, operation_key):
        from rest_framework.test import APIRequestFactory, force_authenticate
        from .hr_screening_api import owned_batch
        from .hr_screening_results import queue
        root, work = self._department_scope("hr")
        arguments = {"batch_id": batch_id, "expected_version": expected_version}
        key = f"hr:queue:{operation_key}"
        previous = AgentBusinessReference.objects.filter(root=root, operation_key=key).first()
        if previous:
            if previous.digest != digest(arguments):
                raise ProductError("idempotency_conflict", "操作键已用于不同内容。", 409)
            return {"batch_id": previous.object_id, "version": int(previous.revision)}
        batch = owned_batch(self._actor(), batch_id, lock=True)
        fields = ("agent_root_id", "agent_work_id", "agent_requirement_version", "agent_grant_version",
                  "agent_session_version", "agent_root_fence")
        if any(not hasattr(batch, field) for field in fields):
            raise ProductError("agent_domain_unavailable", "HR 领域提交守卫尚未接入。", 503)
        if batch.agent_root_id and (str(batch.agent_root_id) != str(root.pk)
                                    or str(batch.agent_work_id) != str(work.pk)
                                    or batch.agent_requirement_version != work.current_requirement_version
                                    or batch.agent_root_fence != self.guard.binding.fence):
            raise ProductError("agent_binding_stale", "HR 批次已关联其他或过期工作。", 409)
        batch.agent_root_id = str(root.pk)
        batch.agent_work_id = str(work.pk)
        batch.agent_requirement_version = work.current_requirement_version
        batch.agent_grant_version = self.guard.binding.grant_version
        batch.agent_session_version = self.guard.binding.session_version
        batch.agent_root_fence = self.guard.binding.fence
        batch.save(update_fields=fields)
        request = APIRequestFactory().post("/api/hr/screening/batches/queue/",
                                            {"expected_version": expected_version}, format="json")
        force_authenticate(request, user=self._actor())
        response = queue(request, batch_id)
        if response.status_code >= 400:
            raise ProductError("hr_queue_rejected", response.data.get("detail", "HR 筛选启动失败。"),
                               response.status_code)
        batch.refresh_from_db(fields=["version", "status"])
        AgentBusinessReference.objects.create(
            root=root, work=work, requirement=root.requirement, domain_type="resume_batch",
            object_id=str(batch.pk), revision=str(batch.version), digest=digest(arguments),
            operation_key=key, public_summary=response.data.get("position_name", "")[:300])
        return {"batch_id": str(batch.pk), "version": batch.version, "status": batch.status}

    @transaction.atomic
    def gm_read_business(self, code, operation_key):
        from .agent_read_sources import read_digest, register_read_sources
        from .business_boards import (BOARDS, _can_read_version, _checksum,
                                      _visible_board_snapshot, allowed, payload)
        from .business_models import BusinessLedgerWorkbook
        self._reserve("gm_read", operation_key)
        if not isinstance(code, str) or code not in BOARDS:
            raise ProductError("agent_department_unavailable", "经营引用不可访问。", 403)
        actor = self._manager_actor()
        revision = _visible_board_snapshot(code)
        if revision is not None:
            snapshot = SimpleNamespace(department=code, state=revision.state, as_of=revision.as_of,
                                       records=revision.records)
            if (not allowed(actor) or revision.state != BusinessLedgerWorkbook.State.PUBLISHED
                    or not _can_read_version(actor, revision)
                    or _checksum(snapshot, records=revision.records, state=revision.state) != revision.checksum):
                raise AgentDenied("source_authorization_changed")
        result = payload(code, revision)
        if revision is not None:
            register_read_sources(self._read_source_root(), [{"kind": "gm_board",
                "revision_id": str(revision.pk), "department": code,
                "revision": revision.revision, "checksum": revision.checksum}])
        return result

    @transaction.atomic
    def gm_list_work(self, department_code="", offset=0, operation_key=None):
        from .agent_api import listing, management_references
        self._reserve("gm_list_work", operation_key)
        if department_code is None:
            department_code = ""
        if (not isinstance(department_code, str)
                or department_code not in {"", "product", "hr", "finance", "engineering"}
                or type(offset) is not int or offset < 0):
            raise ProductError("invalid_management_filter", "管理摘要筛选无效。")
        self._manager_actor()
        query = AgentWorkTask.objects.all()
        if department_code:
            query = query.filter(department_code=department_code)
        return listing(query.order_by("-created_at"), offset, 20, lambda item: {
            "id": str(item.pk), "owner_id": item.owner_id, "department_code": item.department_code,
            "summary": item.public_summary, "state": item.state, "updated_at": item.updated_at.isoformat(),
            "business_references": management_references(item)})

    @transaction.atomic
    def gm_read_reference(self, reference_id, operation_key):
        from .agent_api import AgentApiError
        from .agent_management import _reference, _resolve
        from .agent_read_sources import register_read_sources
        self._reserve("gm_read_reference", operation_key)
        actor = self._manager_actor()
        try:
            reference = _reference(reference_id)
            resolved = _resolve(actor, reference)
        except AgentApiError as error:
            raise ProductError("gm_reference_unavailable", error.detail, error.status) from None
        register_read_sources(self._read_source_root(), [{"kind": "gm_reference",
            "reference_id": str(reference.pk), "domain_type": reference.domain_type,
            "object_id": reference.object_id, "revision": reference.revision,
            "digest": reference.digest}])
        url = f"/api/agent/management/references/{reference.pk}/"
        result = {"reference_id": str(reference.pk), "domain_type": reference.domain_type,
                  "object_id": reference.object_id, "revision": reference.revision,
                  "digest": reference.digest, "summary": reference.public_summary,
                  "data": resolved["data"], "url": url}
        if resolved.get("file") is not None:
            result["download_url"] = url + "download/"
        return result

    @database_boundary
    def execute(self, name, arguments, operation_key):
        names = {
            "begin_work": (self.begin_work, set()),
            "product_create_task": (self.create_product_task, {"title"}),
            "product_queue_blueprint": (self.queue_product_blueprint, {"task_id", "expected_version"}),
            "product_queue_outputs": (self.queue_product_outputs, {"task_id", "expected_version"}),
            "product_read_task": (self.read_product_task, {"task_id"}),
            "product_read_source": (self.read_product_source, {"task_id", "source_id", "offset"}),
            "product_search_sources": (self.search_product_sources, {"task_id", "query"}),
            "product_list_outputs": (self.list_product_outputs, {"task_id"}),
            "finance_read_drafts": (self.finance_read_drafts, set()),
            "finance_save_draft": (self.finance_save_draft, {"record", "expected_revision"}),
            "hr_read_jd": (self.hr_read_jd, {"request_id"}),
            "hr_read_batch": (self.hr_read_batch, {"batch_id", "offset"}),
            "hr_queue_batch": (self.hr_queue_batch, {"batch_id", "expected_version"}),
            "gm_read_business": (self.gm_read_business, {"code"}),
            "gm_list_work": (self.gm_list_work, {"department_code", "offset"}),
            "gm_read_reference": (self.gm_read_reference, {"reference_id"}),
        }
        if name not in names or not isinstance(arguments, dict) or set(arguments) != names[name][1]:
            raise ProductError("agent_tool_unavailable", "工具或参数未获授权。", 403)
        return names[name][0](**arguments, operation_key=operation_key)


def tools_for_run(guard: RuntimeGuard, *, message_id=None):
    from langchain.tools import ToolRuntime, tool

    service = AgentTools(guard, message_id=message_id)

    @tool
    def begin_work(runtime: ToolRuntime) -> dict:
        """Turn the current user message into a tracked work item before business writes."""
        return service.execute("begin_work", {}, runtime.tool_call_id)

    @tool
    def product_create_task(title: str, runtime: ToolRuntime) -> dict:
        """Create a product document task bound to the current work; source uploads remain a user action."""
        return service.execute("product_create_task", {"title": title}, runtime.tool_call_id)

    @tool
    def product_read_task(task_id: str, runtime: ToolRuntime) -> dict:
        """Read an authorized product task, its source summaries, blueprint and output status."""
        return service.execute("product_read_task", {"task_id": task_id}, runtime.tool_call_id)

    @tool
    def product_read_source(task_id: str, source_id: str, offset: int, runtime: ToolRuntime) -> dict:
        """Read a bounded segment of an authorized uploaded source extraction."""
        return service.execute("product_read_source", {"task_id": task_id, "source_id": source_id,
                                                       "offset": offset}, runtime.tool_call_id)

    @tool
    def product_search_sources(task_id: str, query: str, runtime: ToolRuntime) -> list:
        """Search the current task's uploaded source extractions without external requests."""
        return service.execute("product_search_sources", {"task_id": task_id, "query": query}, runtime.tool_call_id)

    @tool
    def product_queue_blueprint(task_id: str, expected_version: int, runtime: ToolRuntime) -> dict:
        """Use the existing product worker to build a blueprint from uploaded sources."""
        return service.execute("product_queue_blueprint", {"task_id": task_id,
                                                          "expected_version": expected_version}, runtime.tool_call_id)

    @tool
    def product_queue_outputs(task_id: str, expected_version: int, runtime: ToolRuntime) -> dict:
        """Generate Word technical and feasibility reports plus PPT after the owner confirms the blueprint."""
        return service.execute("product_queue_outputs", {"task_id": task_id,
                                                        "expected_version": expected_version}, runtime.tool_call_id)

    @tool
    def product_list_outputs(task_id: str, runtime: ToolRuntime) -> list:
        """List versioned, authorized product deliverables without exposing storage paths."""
        return service.execute("product_list_outputs", {"task_id": task_id}, runtime.tool_call_id)

    @tool
    def finance_read_drafts(runtime: ToolRuntime) -> dict:
        """Read only the finance records authored by the current employee."""
        return service.execute("finance_read_drafts", {}, runtime.tool_call_id)

    @tool
    def finance_save_draft(record: dict, expected_revision: int, runtime: ToolRuntime) -> dict:
        """Save an own finance draft using the current ledger revision; never publish or delete."""
        return service.execute("finance_save_draft", {"record": record,
                "expected_revision": expected_revision}, runtime.tool_call_id)

    @tool
    def hr_read_jd(request_id: str, runtime: ToolRuntime) -> dict:
        """Read the employee's own recruitment request and current JD versions."""
        return service.execute("hr_read_jd", {"request_id": request_id}, runtime.tool_call_id)

    @tool
    def hr_read_batch(batch_id: str, offset: int, runtime: ToolRuntime) -> dict:
        """Read a bounded page of the employee's own screening batch and results."""
        return service.execute("hr_read_batch", {"batch_id": batch_id,
                "offset": offset}, runtime.tool_call_id)

    @tool
    def hr_queue_batch(batch_id: str, expected_version: int, runtime: ToolRuntime) -> dict:
        """Start screening only when the HR batch and worker support shared root fencing."""
        return service.execute("hr_queue_batch", {"batch_id": batch_id,
                "expected_version": expected_version}, runtime.tool_call_id)

    @tool
    def gm_read_business(code: str, runtime: ToolRuntime) -> dict:
        """Read a published business board allowed for the general manager; no writes."""
        return service.execute("gm_read_business", {"code": code}, runtime.tool_call_id)

    @tool
    def gm_list_work(runtime: ToolRuntime, department_code: str = "", offset: int = 0) -> dict:
        """List public work summaries and the six existing manager-approved reference types."""
        return service.execute("gm_list_work", {"department_code": department_code,
                "offset": offset}, runtime.tool_call_id)

    @tool
    def gm_read_reference(reference_id: str, runtime: ToolRuntime) -> dict:
        """Read one existing manager-approved business reference and its safe download link."""
        return service.execute("gm_read_reference", {"reference_id": reference_id}, runtime.tool_call_id)

    department = service._department()
    if department == "product":
        return [begin_work, product_create_task, product_read_task, product_read_source,
                product_search_sources, product_queue_blueprint, product_queue_outputs, product_list_outputs]
    if department == "finance":
        return [begin_work, finance_read_drafts, finance_save_draft]
    if department == "hr":
        return [begin_work, hr_read_jd, hr_read_batch, hr_queue_batch]
    if department == "":
        return [gm_read_business, gm_list_work, gm_read_reference]
    return []
