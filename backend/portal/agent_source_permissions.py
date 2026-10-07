import re


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def check_sources(root):
    from .agent_runtime import AgentDenied

    def deny():
        raise AgentDenied("source_authorization_changed")

    try:
        from .agent_models import AgentAttachment, AgentBusinessReference, AgentMessage, AgentRun

        persisted_root = AgentRun.objects.select_related("conversation").get(
            pk=root.pk, root_run_id=root.pk, parent__isnull=True)
        owner_id = persisted_root.conversation.owner_id
        conversation_id = persisted_root.conversation_id
        if persisted_root.conversation.root_run_id != persisted_root.pk:
            deny()

        from .product_storage import verified_artifact

        for message in AgentMessage.objects.filter(conversation_id=conversation_id).only(
                "attachment_references"):
            references = message.attachment_references
            if not isinstance(references, list):
                deny()
            for reference in references:
                if (not isinstance(reference, dict)
                        or set(reference) != {"type", "id", "sha256"}
                        or reference["type"] != "agent_attachment"
                        or not _SHA256.fullmatch(str(reference["sha256"]))):
                    deny()
                attachment = AgentAttachment.objects.filter(
                    pk=reference["id"], owner_id=owner_id).first()
                if (attachment is None or attachment.deleted_at is not None
                        or attachment.source_scope != "private"
                        or attachment.sha256 != reference["sha256"]):
                    deny()
                verified_artifact(attachment)

        references = AgentBusinessReference.objects.filter(root=persisted_root).select_related(
            "work", "work__owner", "requirement", "requirement__user_message")
        for reference in references:
            if (reference.work.owner_id != owner_id
                    or reference.work.conversation_id != conversation_id
                    or reference.requirement.work_id != reference.work_id
                    or reference.requirement.user_message.conversation_id != conversation_id
                    or reference.requirement.user_message.role != "user"
                    or reference.requirement.user_message.work_id not in (None, reference.work_id)
                    or not reference.object_id or not reference.revision
                    or not _SHA256.fullmatch(reference.digest)):
                deny()

            kind = reference.domain_type
            if kind == "finance_record":
                _check_finance_record(reference, persisted_root, owner_id, deny)
            elif kind == "resume_batch":
                _check_resume_batch(reference, persisted_root, owner_id, deny)
            elif kind == "resume_artifact":
                _check_resume_artifact(reference, persisted_root, owner_id, deny)
            elif kind in {"document_task", "document_source", "document_artifact", "source", "artifact"}:
                _check_product_reference(reference, persisted_root, owner_id, verified_artifact, deny)
            elif kind == "business_revision":
                _check_business_revision(reference, persisted_root, deny)
            else:
                deny()

        _check_root_product_sources(persisted_root, owner_id, conversation_id,
                                    verified_artifact, deny)
        from .agent_read_sources import check_read_sources
        check_read_sources(persisted_root)
    except AgentDenied:
        raise
    except Exception:
        raise AgentDenied("source_authorization_changed") from None


def _product_task(reference, root, owner_id, deny):
    from .product_models import DocumentTask

    task = DocumentTask.objects.filter(pk=reference.object_id, owner_id=owner_id).first()
    if (task is None or task.agent_root_id != str(root.pk)
            or task.agent_work_id != str(reference.work_id)):
        deny()
    return task


def _check_root_product_sources(root, owner_id, conversation_id, verified_artifact, deny):
    from .agent_models import AgentWorkTask
    from .product_models import DocumentSource, DocumentTask
    from .product_service import input_authorized

    tasks = DocumentTask.objects.filter(agent_root_id=str(root.pk)).select_related("owner")
    for task in tasks:
        work = AgentWorkTask.objects.filter(pk=task.agent_work_id, owner_id=owner_id,
            conversation_id=conversation_id, department_code="product").first()
        if (task.owner_id != owner_id or work is None
                or root.conversation.department_code != "product"):
            deny()
        _agent_department(task.owner, deny, expected="product")
        revisions = list(task.revisions.filter(kind="input").order_by("version", "created_at", "pk"))
        if not revisions or task.input_version < 1:
            deny()
        expected_version = 1
        for revision in revisions:
            if (revision.version != expected_version or not isinstance(revision.payload, dict)
                    or not input_authorized(task, revision)):
                deny()
            expected_version += 1
            sources = revision.payload.get("sources", [])
            if not isinstance(sources, list):
                deny()
            for snapshot in sources:
                if (not isinstance(snapshot, dict)
                        or not isinstance(snapshot.get("id"), str)
                        or not _SHA256.fullmatch(str(snapshot.get("sha256", "")))):
                    deny()
                source = DocumentSource.objects.filter(
                    pk=snapshot["id"], task=task, task__owner_id=owner_id).first()
                if source is None or source.sha256 != snapshot["sha256"]:
                    deny()
        if revisions[-1].version != task.input_version:
            deny()
        for source in DocumentSource.objects.filter(task=task):
            _check_product_source(source, task, root, work.pk, owner_id, verified_artifact, deny)


def _check_product_reference(reference, root, owner_id, verified_artifact, deny):
    from .product_models import DocumentArtifact, DocumentRevision, DocumentSource
    from .product_service import input_authorized

    kind = reference.domain_type
    if (reference.work.department_code != "product"
            or root.conversation.department_code != "product"):
        deny()
    _agent_department(reference.work.owner, deny, expected="product")
    if kind == "document_task":
        task = _product_task(reference, root, owner_id, deny)
        if not reference.revision.isdecimal() or int(reference.revision) < 1:
            deny()
        return

    if kind in {"document_source", "source"}:
        source = DocumentSource.objects.select_related("task").filter(pk=reference.object_id).first()
        if (source is None or source.task.owner_id != owner_id
                or source.sha256 != reference.revision or source.sha256 != reference.digest):
            deny()
        _check_product_source(source, source.task, root, reference.work_id, owner_id,
                              verified_artifact, deny)
        return

    artifact = DocumentArtifact.objects.select_related("task").filter(
        pk=reference.object_id).first()
    if (artifact is None or artifact.task.owner_id != owner_id
            or artifact.task.agent_root_id != str(root.pk)
            or artifact.task.agent_work_id != str(reference.work_id)
            or reference.revision != str(artifact.version)
            or reference.digest != artifact.sha256):
        deny()
    input_revision = artifact.task.revisions.filter(kind=DocumentRevision.Kind.INPUT,
        sha256=artifact.input_hash).first()
    if input_revision is None or not input_authorized(artifact.task, input_revision):
        deny()
    verified_artifact(artifact)


def _check_product_source(source, task, root, work_id, owner_id, verified_artifact, deny):
    from .product_models import DocumentRevision
    from .product_service import input_authorized

    if (task.owner_id != owner_id or task.agent_root_id != str(root.pk)
            or task.agent_work_id != str(work_id)):
        deny()
    registered = []
    for revision in task.revisions.filter(kind=DocumentRevision.Kind.INPUT).order_by(
            "version", "created_at", "pk"):
        snapshots = revision.payload.get("sources", [])
        if not isinstance(snapshots, list):
            deny()
        matching = [item for item in snapshots
                    if isinstance(item, dict) and str(item.get("id")) == str(source.pk)]
        if any(item.get("sha256") != source.sha256 for item in matching):
            deny()
        if matching:
            registered.append(revision)
    if (source.task_id != task.pk or not registered
            or any(not input_authorized(task, revision) for revision in registered)):
        deny()
    verified_artifact(source)


def _check_finance_record(reference, root, owner_id, deny):
    from .business_boards import _grant, _row_checksum
    from .business_models import BusinessLedgerWorkbook
    from .models import User

    owner = User.objects.filter(pk=owner_id).first()
    workbook = BusinessLedgerWorkbook.objects.filter(department="finance").first()
    if (owner is None or reference.work.department_code != "finance"
            or root.conversation.department_code != "finance"
            or _agent_department(owner, deny, expected="finance") != "finance"
            or _grant(owner, "finance") is None or workbook is None
            or not reference.revision.isdecimal()
            or not 1 <= int(reference.revision) <= workbook.revision):
        deny()
    metadata = [item for item in workbook.record_meta.values()
                if isinstance(item, dict) and item.get("project_id") == reference.object_id
                and item.get("record_author_id") == owner_id and not item.get("deleted")]
    rows = [item for item in workbook.records
            if isinstance(item, dict) and item.get("project_id") == reference.object_id]
    if len(metadata) != 1 or len(rows) != 1:
        deny()
    item = metadata[0]
    if (item.get("draft_actor_id") != owner_id
            or item.get("draft_checksum") != _row_checksum(rows[0])):
        deny()


def _check_resume_batch(reference, root, owner_id, deny):
    from .hr_recruitment_models import RecruitmentRequest
    from .hr_retention import active_artifacts, active_batches
    from .hr_resume_storage import read_file
    from .hr_screening_models import ResumeArtifact, ResumeScreeningBatch

    if (reference.work.department_code != "hr"
            or root.conversation.department_code != "hr"):
        deny()
    _agent_department(root.conversation.owner, deny, expected="hr")
    batch = ResumeScreeningBatch.objects.select_related("jd_version__request").filter(
        pk=reference.object_id, created_by_id=owner_id, agent_root_id=str(root.pk),
        agent_work_id=str(reference.work_id)).first()
    if (batch is None or not reference.revision.isdecimal()
            or int(reference.revision) < 1
            or batch.jd_version.request.created_by_id != owner_id
            or not active_batches(ResumeScreeningBatch.objects.filter(pk=batch.pk)).exists()):
        deny()
    if not RecruitmentRequest.objects.filter(pk=batch.jd_version.request_id,
                                              created_by_id=owner_id,
                                              archive_state="active").exists():
        deny()
    artifacts = list(ResumeArtifact.objects.filter(batch=batch))
    active = list(active_artifacts(ResumeArtifact.objects.filter(batch=batch)))
    if len(artifacts) != len(active):
        deny()
    for artifact in active:
        if artifact.uploaded_by_id != owner_id:
            deny()
        read_file(artifact.file_id, artifact.sha256)


def _check_resume_artifact(reference, root, owner_id, deny):
    from .hr_retention import active_artifacts, active_batches
    from .hr_resume_storage import read_file
    from .hr_screening_models import ResumeArtifact, ResumeScreeningBatch

    owner = root.conversation.owner
    if (reference.work.department_code != "hr"
            or root.conversation.department_code != "hr"
            or reference.work.owner_id != owner_id):
        deny()
    _agent_department(owner, deny, expected="hr")
    artifact = ResumeArtifact.objects.select_related(
        "batch__jd_version__request").filter(pk=reference.object_id).first()
    if artifact is None:
        deny()
    batch = artifact.batch
    if (artifact.uploaded_by_id != owner_id
            or batch.created_by_id != owner_id
            or batch.jd_version.created_by_id != owner_id
            or batch.jd_version.request.created_by_id != owner_id
            or batch.agent_root_id != str(root.pk)
            or batch.agent_work_id != str(reference.work_id)
            or not reference.revision.isdecimal()
            or int(reference.revision) < 1
            or reference.revision != str(artifact.version)
            or reference.digest != artifact.sha256
            or not active_batches(ResumeScreeningBatch.objects.filter(pk=batch.pk)).exists()
            or not active_artifacts(ResumeArtifact.objects.filter(pk=artifact.pk)).exists()):
        deny()
    read_file(artifact.file_id, artifact.sha256)


def _check_business_revision(reference, root, deny):
    from types import SimpleNamespace
    from .business_boards import BOARDS, _can_read_version, _checksum, _grant, allowed
    from .business_models import BusinessLedgerRevision, BusinessLedgerWorkbook

    owner = root.conversation.owner
    department = root.conversation.department_code
    if department == "finance":
        if reference.work.department_code != "finance":
            deny()
        _agent_department(owner, deny, expected="finance")
        if _grant(owner, "finance", "can_edit") is None:
            deny()
        revision = BusinessLedgerRevision.objects.select_related("workbook").filter(
            pk=reference.object_id, workbook__department="finance").first()
        if (revision is None
                or revision.state != BusinessLedgerWorkbook.State.PUBLISHED
                or revision.actor_id != owner.pk
                or revision.action != "publish"
                or not 1 <= revision.revision <= revision.workbook.revision
                or not reference.revision.isdecimal()
                or int(reference.revision) < 1
                or reference.revision != str(revision.revision)
                or reference.digest != revision.checksum):
            deny()
        snapshot = SimpleNamespace(department=revision.workbook.department,
            state=revision.state, as_of=revision.as_of, records=revision.records)
        if revision.checksum != _checksum(snapshot):
            deny()
        return

    if department or reference.work.department_code:
        deny()
    _agent_department(owner, deny, expected="")
    revision = BusinessLedgerRevision.objects.select_related("workbook").filter(
        pk=reference.object_id, workbook__department__in=BOARDS).first()
    if (revision is None or not allowed(owner) or not _can_read_version(owner, revision)
            or not reference.revision.isdecimal()
            or reference.revision != str(revision.revision)
            or reference.digest != revision.checksum):
        deny()


def _agent_department(owner, deny, expected):
    from types import SimpleNamespace
    from .agent_api import AgentApiError, actor, department

    try:
        value = department(actor(SimpleNamespace(agent_user=owner)))
    except AgentApiError:
        deny()
    if value != expected:
        deny()
    return value
