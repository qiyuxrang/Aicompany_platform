import copy
import hashlib
import json
import re
import uuid
from types import SimpleNamespace

from django.core import signing
from django.db import transaction

from .agent_runtime import AgentDenied


_SALT = "agent-read-source-pointer"
_HASH = re.compile(r"^[0-9a-f]{64}$")
_FIELDS = {
    "hr_request": {"request_id", "version", "body_sha256"},
    "hr_jd": {"request_id", "jd_id", "version", "input_version", "body_sha256"},
    "hr_batch": {"batch_id", "version", "request_id", "request_version", "request_sha256",
                  "jd_id", "jd_version", "jd_body_sha256", "body_sha256"},
    "hr_resume": {"artifact_id", "batch_id", "version", "sha256", "file_id"},
    "gm_board": {"revision_id", "department", "revision", "checksum"},
    "gm_reference": {"reference_id", "domain_type", "object_id", "revision", "digest"},
    "product_task": {"task_id", "owner_id", "task_version", "input_revision_id", "input_version",
                     "input_sha256"},
    "product_source": {"task_id", "owner_id", "task_version", "input_revision_id", "input_version",
                       "input_sha256", "source_id", "uploaded_by_id", "sha256", "parsed_sha256"},
    "product_artifact": {"task_id", "owner_id", "task_version", "input_revision_id", "input_version",
                         "input_sha256", "artifact_id", "version", "sha256", "input_hash"},
    "finance_draft": {"owner_id", "workbook_id", "revision_id", "revision", "revision_checksum",
                      "record_id", "project_id", "record_author_id", "draft_actor_id", "draft_revision",
                      "draft_checksum", "record_sha256"},
}


def _denied():
    raise AgentDenied("source_authorization_changed")


def _uuid(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def read_digest(value):
    if isinstance(value, str):
        encoded = value.encode("utf-8")
    else:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate(source):
    if type(source) is not dict or source.get("kind") not in _FIELDS:
        _denied()
    kind = source["kind"]
    if set(source) != _FIELDS[kind] | {"kind"}:
        _denied()
    if kind == "hr_request":
        if not _uuid(source["request_id"]) or type(source["version"]) is not int or source["version"] < 1:
            _denied()
        hashes = (source["body_sha256"],)
    elif kind == "hr_jd":
        if (not _uuid(source["request_id"]) or not _uuid(source["jd_id"])
                or type(source["version"]) is not int or source["version"] < 1
                or type(source["input_version"]) is not int or source["input_version"] < 1):
            _denied()
        hashes = (source["body_sha256"],)
    elif kind == "hr_batch":
        if (not _uuid(source["batch_id"]) or not _uuid(source["request_id"])
                or not _uuid(source["jd_id"])
                or any(type(source[key]) is not int or source[key] < 1
                       for key in ("version", "request_version", "jd_version"))):
            _denied()
        hashes = (source["request_sha256"], source["jd_body_sha256"], source["body_sha256"])
    elif kind == "hr_resume":
        if (not _uuid(source["artifact_id"]) or not _uuid(source["batch_id"])
                or not _uuid(source["file_id"]) or type(source["version"]) is not int
                or source["version"] < 1):
            _denied()
        hashes = (source["sha256"],)
    elif kind == "gm_board":
        from .business_boards import BOARDS
        if (not _uuid(source["revision_id"]) or source["department"] not in BOARDS
                or type(source["revision"]) is not int or source["revision"] < 1):
            _denied()
        hashes = (source["checksum"],)
    elif kind == "gm_reference":
        from .agent_api import GM_REFERENCE_TYPES
        if (not _uuid(source["reference_id"]) or source["domain_type"] not in GM_REFERENCE_TYPES
                or not isinstance(source["object_id"], str) or not source["object_id"]
                or len(source["object_id"]) > 160
                or not isinstance(source["revision"], str) or not source["revision"]
                or len(source["revision"]) > 160
                or not isinstance(source["digest"], str) or len(source["digest"]) > 64):
            _denied()
        hashes = (source["digest"],) if source["digest"] else ()
    elif kind in {"product_task", "product_source", "product_artifact"}:
        if (not _uuid(source["task_id"]) or type(source["owner_id"]) is not int or source["owner_id"] < 1
                or type(source["task_version"]) is not int or source["task_version"] < 1
                or type(source["input_version"]) is not int or source["input_version"] < 0):
            _denied()
        if source["input_revision_id"]:
            if (not _uuid(source["input_revision_id"]) or source["input_version"] < 1
                    or not isinstance(source["input_sha256"], str)
                    or not _HASH.fullmatch(source["input_sha256"])):
                _denied()
        elif source["input_version"] != 0 or source["input_sha256"] != "":
            _denied()
        hashes = (source["input_sha256"],) if source["input_sha256"] else ()
        if kind == "product_source":
            if (not _uuid(source["source_id"])
                    or (source["uploaded_by_id"] is not None and
                        (type(source["uploaded_by_id"]) is not int or source["uploaded_by_id"] < 1))):
                _denied()
            hashes += (source["sha256"], source["parsed_sha256"])
        elif kind == "product_artifact":
            if (not _uuid(source["artifact_id"]) or type(source["version"]) is not int
                    or source["version"] < 1 or source["input_hash"] != source["input_sha256"]
                    or not source["input_revision_id"]):
                _denied()
            hashes += (source["sha256"], source["input_hash"])
    else:
        if (type(source["owner_id"]) is not int or source["owner_id"] < 1
                or not _uuid(source["workbook_id"]) or not _uuid(source["revision_id"])
                or type(source["revision"]) is not int or source["revision"] < 1
                or type(source["record_author_id"]) is not int or source["record_author_id"] != source["owner_id"]
                or type(source["draft_actor_id"]) is not int or source["draft_actor_id"] != source["owner_id"]
                or type(source["draft_revision"]) is not int or source["draft_revision"] < 1
                or not isinstance(source["record_id"], str) or not source["record_id"]
                or len(source["record_id"]) > 64
                or not isinstance(source["project_id"], str) or not source["project_id"]
                or len(source["project_id"]) > 200):
            _denied()
        hashes = (source["revision_checksum"], source["draft_checksum"], source["record_sha256"])
        if source["record_sha256"] != source["draft_checksum"]:
            _denied()
    if any(not isinstance(value, str) or not _HASH.fullmatch(value) for value in hashes):
        _denied()


def _source_key(source):
    return json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_sources(root):
    try:
        from .agent_models import AgentRun

        stored = AgentRun.objects.get(pk=root.pk, parent__isnull=True)
        if stored.root_run_id != stored.pk or not isinstance(stored.policy, dict):
            _denied()
        entries = stored.policy.get("read_sources", [])
        if type(entries) is not list:
            _denied()
        result, seen = [], set()
        for entry in entries:
            if (type(entry) is not dict or set(entry) != {"source", "signature"}
                    or not isinstance(entry["signature"], str) or len(entry["signature"]) > 2048):
                _denied()
            source = entry["source"]
            _validate(source)
            signed = signing.loads(entry["signature"], salt=_SALT)
            if signed != {"root_id": str(stored.pk), "source": source}:
                _denied()
            key = _source_key(source)
            if key in seen:
                _denied()
            seen.add(key)
            result.append(source)
        return result
    except AgentDenied:
        raise
    except Exception:
        _denied()


def register_read_sources(root, sources):
    try:
        if type(sources) is not list:
            _denied()
        for source in sources:
            _validate(source)
        from .agent_models import AgentRun

        with transaction.atomic():
            stored = AgentRun.objects.select_for_update().get(pk=root.pk, parent__isnull=True)
            if stored.root_run_id != stored.pk or not isinstance(stored.policy, dict):
                _denied()
            policy = copy.deepcopy(stored.policy)
            existing = read_sources(stored)
            known = {_source_key(source) for source in existing}
            entries = list(policy.get("read_sources", []))
            for source in sources:
                key = _source_key(source)
                if key in known:
                    continue
                signature = signing.dumps({"root_id": str(stored.pk), "source": source},
                                         salt=_SALT, compress=True)
                entries.append({"source": copy.deepcopy(source), "signature": signature})
                known.add(key)
            if entries != policy.get("read_sources", []):
                policy["read_sources"] = entries
                stored.policy = policy
                stored.save(update_fields=["policy"])
    except AgentDenied:
        raise
    except Exception:
        _denied()


def _hr_actor(root):
    from .agent_api import actor, department

    owner = root.conversation.owner
    actor(SimpleNamespace(agent_user=owner))
    if department(owner) != "hr":
        _denied()
    return owner


def _check_hr_request(root, source):
    from .hr_recruitment_api import _data, _owned

    request = _owned(_hr_actor(root), source["request_id"])
    if request.input_version < source["version"]:
        _denied()
    if request.input_version == source["version"] and read_digest(_data(request)) != source["body_sha256"]:
        _denied()


def _check_hr_jd(root, source):
    from .hr_recruitment_api import _owned
    from .hr_recruitment_models import JDVersion

    request = _owned(_hr_actor(root), source["request_id"])
    jd = JDVersion.objects.filter(pk=source["jd_id"], request=request).first()
    if (jd is None or jd.version != source["version"] or jd.input_version != source["input_version"]
            or read_digest(jd.body) != source["body_sha256"]):
        _denied()


def _check_hr_batch(root, source):
    from .hr_screening_api import owned_batch

    owner = _hr_actor(root)
    batch = owned_batch(owner, source["batch_id"])
    request = batch.jd_version.request
    if (request.created_by_id != owner.pk or str(request.pk) != source["request_id"]
            or str(batch.jd_version_id) != source["jd_id"]
            or batch.version < source["version"] or request.input_version < source["request_version"]
            or batch.jd_version.version != source["jd_version"]
            or read_digest(batch.jd_version.body) != source["jd_body_sha256"]):
        _denied()
    if (request.input_version == source["request_version"]
            and read_digest({"id": str(request.pk), "input_version": request.input_version,
                             "position_name": request.position_name}) != source["request_sha256"]):
        _denied()


def _check_hr_resume(root, source):
    from .hr_recruitment_api import _owned
    from .hr_screening_api import owned_batch
    from .hr_resume_storage import read_file
    from .hr_retention import active_artifacts
    from .hr_screening_models import ResumeArtifact

    owner = _hr_actor(root)
    batch = owned_batch(owner, source["batch_id"])
    request = _owned(owner, batch.jd_version.request_id)
    if (batch.created_by_id != owner.pk or request.created_by_id != owner.pk
            or batch.jd_version.request_id != request.pk):
        _denied()
    item = active_artifacts(ResumeArtifact.objects.select_related(
        "batch__jd_version__request").filter(pk=source["artifact_id"], batch=batch)).first()
    if (item is None or item.uploaded_by_id != owner.pk or item.file_id != source["file_id"]
            or item.sha256 != source["sha256"] or item.version < source["version"]):
        _denied()
    read_file(item.file_id, item.sha256)


def _manager(root):
    from .agent_management import _manager

    owner = root.conversation.owner
    if root.conversation.department_code != "":
        _denied()
    _manager(SimpleNamespace(agent_user=owner))
    return owner


def _check_gm_board(root, source):
    from .business_boards import _can_read_version, _checksum, allowed
    from .business_models import BusinessLedgerRevision, BusinessLedgerWorkbook

    owner = _manager(root)
    revision = BusinessLedgerRevision.objects.select_related("workbook").filter(
        pk=source["revision_id"], revision=source["revision"],
        workbook__department=source["department"]).first()
    snapshot = (SimpleNamespace(department=source["department"], state=revision.state,
                                as_of=revision.as_of, records=revision.records) if revision else None)
    if (revision is None or revision.state != BusinessLedgerWorkbook.State.PUBLISHED
            or not allowed(owner) or not _can_read_version(owner, revision)
            or revision.checksum != source["checksum"]
            or _checksum(snapshot, records=revision.records, state=revision.state) != revision.checksum):
        _denied()


def _check_gm_reference(root, source):
    from .agent_management import _reference, _resolve

    owner = _manager(root)
    reference = _reference(source["reference_id"])
    if (reference.domain_type != source["domain_type"] or reference.object_id != source["object_id"]
            or reference.revision != source["revision"] or reference.digest != source["digest"]):
        _denied()
    _resolve(owner, reference)


def _product_actor(root):
    from .agent_api import AgentApiError, actor, department

    owner = root.conversation.owner
    try:
        actor(SimpleNamespace(agent_user=owner))
    except AgentApiError:
        _denied()
    if root.conversation.department_code != "product" or department(owner) != "product":
        _denied()
    return owner


def _check_product_task_pointer(root, source):
    from .product_models import DocumentRevision
    from .product_service import input_authorized, task_for

    owner = _product_actor(root)
    task = task_for(owner, source["task_id"])
    if task.owner_id != source["owner_id"] or task.owner_id != owner.pk or task.version < source["task_version"]:
        _denied()
    if source["input_version"] > task.input_version:
        _denied()
    if source["input_revision_id"]:
        revision = DocumentRevision.objects.filter(
            pk=source["input_revision_id"], task=task, kind=DocumentRevision.Kind.INPUT,
            version=source["input_version"], sha256=source["input_sha256"]).first()
        if (revision is None or read_digest(revision.payload) != revision.sha256
                or not input_authorized(task, revision)):
            _denied()
    return task


def _check_product_task(root, source):
    _check_product_task_pointer(root, source)


def _check_product_source(root, source):
    from .product_models import DocumentSource
    from .product_storage import verified_artifact

    task = _check_product_task_pointer(root, source)
    item = DocumentSource.objects.filter(pk=source["source_id"], task=task).first()
    if (item is None or item.task.owner_id != source["owner_id"] or item.sha256 != source["sha256"]
            or item.uploaded_by_id != source["uploaded_by_id"]
            or read_digest(item.parsed) != source["parsed_sha256"]):
        _denied()
    verified_artifact(item)


def _check_product_artifact(root, source):
    from .product_models import DocumentArtifact
    from .product_storage import verified_artifact

    task = _check_product_task_pointer(root, source)
    item = DocumentArtifact.objects.filter(pk=source["artifact_id"], task=task).first()
    if (item is None or item.task.owner_id != source["owner_id"] or item.version != source["version"]
            or item.sha256 != source["sha256"] or item.input_hash != source["input_hash"]):
        _denied()
    verified_artifact(item)


def _check_finance_draft(root, source):
    from .agent_api import AgentApiError, actor, department
    from .business_boards import _checksum, _grant, _row_checksum
    from .business_models import BusinessLedgerRevision, BusinessLedgerWorkbook

    owner = root.conversation.owner
    if root.conversation.department_code != "finance":
        _denied()
    try:
        actor(SimpleNamespace(agent_user=owner))
    except AgentApiError:
        _denied()
    if department(owner) != "finance" or _grant(owner, "finance") is None or owner.pk != source["owner_id"]:
        _denied()
    workbook = BusinessLedgerWorkbook.objects.filter(pk=source["workbook_id"], department="finance").first()
    snapshot = BusinessLedgerRevision.objects.filter(
        pk=source["revision_id"], workbook=workbook, revision=source["revision"]).first() if workbook else None
    if snapshot is None or snapshot.checksum != source["revision_checksum"]:
        _denied()
    snapshot_value = SimpleNamespace(department=snapshot.workbook.department, state=snapshot.state,
                                      as_of=snapshot.as_of, records=snapshot.records)
    if _checksum(snapshot_value, records=snapshot.records, state=snapshot.state) != snapshot.checksum:
        _denied()

    def row_and_meta(records, metadata):
        meta = metadata.get(source["record_id"])
        rows = [row for row in records if isinstance(row, dict) and row.get("project_id") == source["project_id"]]
        if not isinstance(meta, dict) or len(rows) != 1:
            return None, None
        return rows[0], meta

    snapshot_row, snapshot_meta = row_and_meta(snapshot.records, snapshot.record_meta)
    if (snapshot_meta is None or snapshot_meta.get("deleted")
            or snapshot_meta.get("project_id") != source["project_id"]
            or snapshot_meta.get("record_author_id") != source["record_author_id"]
            or snapshot_meta.get("draft_actor_id") != source["draft_actor_id"]
            or snapshot_meta.get("draft_revision") != source["draft_revision"]
            or snapshot_meta.get("draft_checksum") != source["draft_checksum"]
            or _row_checksum(snapshot_row) != source["record_sha256"]):
        _denied()
    if workbook.revision < source["revision"]:
        _denied()
    current_row, current_meta = row_and_meta(workbook.records, workbook.record_meta)
    if (current_meta is None or current_meta.get("deleted")
            or current_meta.get("project_id") != source["project_id"]
            or current_meta.get("record_author_id") != owner.pk
            or current_meta.get("draft_actor_id") != owner.pk
            or type(current_meta.get("draft_revision")) is not int
            or current_meta["draft_revision"] < source["draft_revision"]
            or current_meta["draft_revision"] > workbook.revision
            or _row_checksum(current_row) != current_meta.get("draft_checksum")):
        _denied()


def check_read_sources(root):
    try:
        handlers = {"hr_request": _check_hr_request, "hr_jd": _check_hr_jd,
                    "hr_batch": _check_hr_batch, "hr_resume": _check_hr_resume,
                    "gm_board": _check_gm_board, "gm_reference": _check_gm_reference,
                    "product_task": _check_product_task, "product_source": _check_product_source,
                    "product_artifact": _check_product_artifact, "finance_draft": _check_finance_draft}
        for source in read_sources(root):
            handlers[source["kind"]](root, source)
    except AgentDenied as error:
        if str(error) == "source_authorization_changed":
            raise
        _denied()
    except Exception:
        _denied()
