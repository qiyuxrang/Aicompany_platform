import re
from io import BytesIO
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.http import FileResponse
from django.urls import path
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .agent_api import endpoint, fail
from .agent_models import AgentBusinessReference
from .business_boards import _can_read_version, _checksum, allowed as business_allowed
from .business_models import BusinessLedgerRevision
from .hr_retention import active_artifacts, active_batches
from .hr_resume_storage import read_file
from .hr_screening_models import ResumeArtifact, ResumeScreeningBatch
from .product_models import DocumentArtifact, DocumentRevision, DocumentSource, DocumentTask
from .product_service import input_authorized
from .product_storage import StorageError, verified_artifact
from .security import audit


def _unavailable(code="reference_unavailable", detail="业务引用不存在或来源授权已失效。", status=404):
    fail(code, detail, status)


def _manager(request):
    user = request.agent_user
    if not business_allowed(user):
        fail("forbidden", "当前账号没有有效的总经理业务只读授权。", 403)
    return user


def _reference(reference_id):
    try:
        reference = AgentBusinessReference.objects.select_related(
            "work__owner", "work__conversation__owner", "root__conversation__owner",
            "requirement__user_message",
        ).get(pk=reference_id)
    except (AgentBusinessReference.DoesNotExist, ValueError, TypeError):
        _unavailable()
    work, root, requirement = reference.work, reference.root, reference.requirement
    message = requirement.user_message
    if (work.owner_id != work.conversation.owner_id
            or work.owner_id != root.conversation.owner_id
            or work.conversation_id != root.conversation_id
            or root.parent_id is not None or root.root_run_id != root.pk
            or requirement.work_id != work.pk
            or message.conversation_id != work.conversation_id or message.work_id != work.pk):
        _unavailable()
    return reference


def _exact_revision(reference, actual):
    if reference.revision != str(actual):
        _unavailable("stale_reference", "业务对象版本与引用登记版本不一致，请重新登记精确版本。", 409)


def _object(model, object_id, *related):
    try:
        return model.objects.select_related(*related).get(pk=object_id)
    except (model.DoesNotExist, ValidationError, ValueError, TypeError):
        _unavailable()


def _product_task(reference):
    task = _object(DocumentTask, reference.object_id, "owner")
    _product_task_for_child(reference, task)
    _exact_revision(reference, task.version)
    return task


def _product_task_for_child(reference, task):
    if (task.owner_id != reference.work.owner_id
            or task.agent_root_id != str(reference.root_id)
            or task.agent_work_id != str(reference.work_id)
            or task.agent_requirement_version != reference.requirement.version):
        _unavailable()
    revision = task.revisions.filter(kind=DocumentRevision.Kind.INPUT, version=task.input_version).first()
    if revision is None or not input_authorized(task, revision):
        _unavailable("source_permission_changed", "产品资料版本缺失或来源授权已失效。", 409)


def _verified_product_file(artifact):
    try:
        return verified_artifact(artifact)
    except StorageError as error:
        _unavailable("source_integrity", "业务文件缺失或完整性校验失败。", 409)


def _document_task(reference):
    task = _product_task(reference)
    return {"data": {"id": str(task.pk), "title": task.title, "state": task.state,
                      "version": task.version, "input_version": task.input_version,
                      "summary": reference.public_summary}, "file": None}


def _document_source(reference):
    source = _object(DocumentSource, reference.object_id, "task__owner")
    task = source.task
    _product_task_for_child(reference, task)
    _exact_revision(reference, source.sha256)
    target = _verified_product_file(source)
    return {"data": {"id": str(source.pk), "task_id": str(task.pk), "name": source.original_name,
                      "purpose": source.purpose, "media_type": source.media_type, "size": source.size,
                      "sha256": source.sha256, "summary": reference.public_summary},
            "file": ("path", target, source.original_name)}


def _document_artifact(reference):
    artifact = _object(DocumentArtifact, reference.object_id, "task__owner")
    task = artifact.task
    _product_task_for_child(reference, task)
    _exact_revision(reference, artifact.version)
    input_revision = task.revisions.filter(kind=DocumentRevision.Kind.INPUT, sha256=artifact.input_hash).first()
    if input_revision is None or not input_authorized(task, input_revision):
        _unavailable("source_permission_changed", "成果所用资料版本缺失或来源授权已失效。", 409)
    target = _verified_product_file(artifact)
    filename = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", f"{task.title}-{artifact.family}-v{artifact.version}").strip(" .")
    filename = (filename or "产品成果")[:180] + target.suffix
    return {"data": {"id": str(artifact.pk), "task_id": str(task.pk), "family": artifact.family,
                      "version": artifact.version, "sha256": artifact.sha256,
                      "input_hash": artifact.input_hash, "filename": filename,
                      "summary": reference.public_summary},
            "file": ("path", target, filename)}


def _hr_batch(reference):
    batch = active_batches(ResumeScreeningBatch.objects.select_related(
        "jd_version__request").filter(pk=reference.object_id, created_by_id=reference.work.owner_id)).first()
    if (batch is None or batch.jd_version.request.created_by_id != reference.work.owner_id):
        _unavailable("source_permission_changed", "招聘批次不存在或归档授权已失效。", 404)
    _exact_revision(reference, batch.version)
    request = batch.jd_version.request
    return {"data": {"id": str(batch.pk), "version": batch.version, "status": batch.status,
                      "stale": batch.stale, "requirements": batch.requirements,
                      "position": {"name": request.position_name, "headcount": request.headcount,
                                   "responsibilities": request.responsibilities,
                                   "required_requirements": request.required_requirements,
                                   "preferred_requirements": request.preferred_requirements,
                                   "salary": request.salary, "benefits": request.benefits,
                                   "original_text": request.original_text},
                      "job_description": batch.jd_version.body,
                      "summary": reference.public_summary}, "file": None}


def _resume_artifact(reference):
    artifact = active_artifacts(ResumeArtifact.objects.select_related(
        "batch__jd_version__request").filter(pk=reference.object_id,
                                              uploaded_by_id=reference.work.owner_id)).first()
    if (artifact is None or artifact.batch.created_by_id != reference.work.owner_id
            or artifact.batch.jd_version.request.created_by_id != reference.work.owner_id):
        _unavailable("source_permission_changed", "简历不存在或归档授权已失效。", 404)
    _exact_revision(reference, artifact.version)
    try:
        content = read_file(artifact.file_id, artifact.sha256)
    except StorageError:
        _unavailable("source_integrity", "简历文件缺失、已删除或完整性校验失败。", 409)
    return {"data": {"id": str(artifact.pk), "batch_id": str(artifact.batch_id),
                      "filename": artifact.filename, "version": artifact.version,
                      "size": artifact.size, "sha256": artifact.sha256,
                      "summary": reference.public_summary},
            "file": ("bytes", content, artifact.filename)}


def _business_revision(request_user, reference):
    revision = _object(BusinessLedgerRevision, reference.object_id, "workbook", "actor")
    if revision.actor_id != reference.work.owner_id:
        _unavailable()
    _exact_revision(reference, revision.revision)
    if not _can_read_version(request_user, revision):
        _unavailable("source_permission_changed", "经营数据版本未发布或当前不可读取。", 404)
    snapshot = SimpleNamespace(department=revision.workbook.department, state=revision.state,
                               as_of=revision.as_of, records=revision.records)
    if (reference.digest != revision.checksum
            or _checksum(snapshot, records=revision.records, state=revision.state) != revision.checksum):
        _unavailable("source_integrity", "经营数据版本完整性校验失败。", 409)
    return {"data": {"id": str(revision.pk), "department": revision.workbook.department,
                      "revision": revision.revision, "state": revision.state, "action": revision.action,
                      "as_of": revision.as_of.isoformat() if revision.as_of else None,
                      "source_name": revision.source_name, "records": revision.records,
                      "checksum": revision.checksum, "summary": reference.public_summary},
            "file": None}


def _resolve(request_user, reference):
    resolvers = {
        "document_task": _document_task,
        "document_source": _document_source,
        "document_artifact": _document_artifact,
        "resume_batch": _hr_batch,
        "resume_artifact": _resume_artifact,
        "business_revision": lambda item: _business_revision(request_user, item),
    }
    resolver = resolvers.get(reference.domain_type)
    if resolver is None:
        _unavailable("reference_type_unsupported", "此业务引用类型不在GM只读白名单中。", 404)
    resolved = resolver(reference)
    return {"reference_id": str(reference.pk), "domain_type": reference.domain_type,
            "object_id": reference.object_id, "revision": reference.revision,
            "summary": reference.public_summary, **resolved}


@api_view(["GET"])
@endpoint
def reference_detail(request, refid):
    user = _manager(request)
    reference = _reference(refid)
    resolved = _resolve(user, reference)
    audit(user, "agent_management_reference_read", f"{reference.pk}:{reference.domain_type}:{reference.revision}")
    return Response({key: value for key, value in resolved.items() if key != "file"})


@api_view(["GET"])
@endpoint
def reference_download(request, refid):
    user = _manager(request)
    reference = _reference(refid)
    resolved = _resolve(user, reference)
    file = resolved["file"]
    if file is None:
        _unavailable("download_unavailable", "此引用没有直接登记的安全原件。", 404)
    kind, value, filename = file
    stream = value.open("rb") if kind == "path" else BytesIO(value)
    response = FileResponse(stream, as_attachment=True, filename=filename,
                            content_type="application/octet-stream")
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    audit(user, "agent_management_reference_download",
          f"{reference.pk}:{reference.domain_type}:{reference.revision}")
    return response


urlpatterns = [
    path("management/references/<uuid:refid>/", reference_detail),
    path("management/references/<uuid:refid>/download/", reference_download),
]
