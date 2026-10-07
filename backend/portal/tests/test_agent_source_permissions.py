import hashlib
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path

from django.test import override_settings
from django.utils import timezone

from portal.agent_models import (AgentAttachment, AgentBusinessReference, AgentConversation,
                                 AgentMessage, AgentRequirement, AgentRun, AgentWorkTask)
from portal.agent_runtime import AgentDenied
from portal.agent_source_permissions import check_sources
from portal.business_models import (BusinessLedgerGrant, BusinessLedgerRevision,
                                    BusinessLedgerWorkbook)
from portal.hr_recruitment_models import JDVersion, RecruitmentRequest
from portal.hr_screening_models import ResumeArtifact, ResumeScreeningBatch
from portal.models import Role
from portal.product_models import DocumentArtifact, DocumentRevision, DocumentSource, DocumentTask
from portal.product_retrieval import _authorization
from portal.product_storage import resolve_relative, write_source
from portal.business_boards import _checksum as _workbook_checksum, _row_checksum
from portal.hr_resume_storage import save_file

from .base import PortalTestCase


class AgentSourcePermissionTests(PortalTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        settings_override = override_settings(PRODUCT_STORAGE_ROOT=root / "product",
                                              HR_STORAGE_ROOT=root / "hr",
                                              PRODUCT_RETRIEVAL_ENABLED=True)
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        self.owner = self.create_user("source-owner", "product")
        self.owner.department_code = "product"
        self.owner.save(update_fields=["department_code"])
        self.root, self.work, self.message, self.requirement = self._new_scope(self.owner, "product")

    def _set_department(self, department, role=None):
        self.owner.department_code = department
        self.owner.save(update_fields=["department_code"])
        if role:
            self.owner.roles.set(Role.objects.filter(code=role))
        self.root.conversation.department_code = department
        self.root.conversation.save(update_fields=["department_code"])
        self.work.department_code = department
        self.work.save(update_fields=["department_code"])

    @staticmethod
    def _new_scope(owner, department):
        conversation = AgentConversation.objects.create(owner=owner, department_code=department)
        work = AgentWorkTask.objects.create(owner=owner, department_code=department,
                                            conversation=conversation, goal="source guard",
                                            state="running", current_requirement_version=1)
        message = AgentMessage.objects.create(conversation=conversation, work=work,
                                              role="user", content="use registered sources")
        requirement = AgentRequirement.objects.create(work=work, version=1,
            user_message=message, content=message.content)
        root = AgentRun.objects.create(id=conversation.root_run_id,
            root_run_id=conversation.root_run_id, conversation=conversation,
            work=work, requirement=requirement, state="running")
        return root, work, message, requirement

    def _reference(self, kind, object_id, revision, *, digest=None, root=None,
                   work=None, requirement=None):
        root = root or self.root
        work = work or root.work
        requirement = requirement or root.requirement
        AgentBusinessReference.objects.create(root=root, work=work, requirement=requirement,
            domain_type=kind, object_id=str(object_id), revision=str(revision),
            digest=digest or hashlib.sha256(kind.encode()).hexdigest(),
            operation_key=str(uuid.uuid4()))

    def _product_source(self, *, retrieval=False, register=True):
        task = DocumentTask.objects.create(owner=self.owner, title="source guard",
            idempotency_key=str(uuid.uuid4()), payload_hash="a" * 64, input_version=1,
            agent_root_id=str(self.root.pk), agent_work_id=str(self.work.pk),
            agent_requirement_version=1)
        content = b"registered product source"
        _, path, checksum = write_source(str(task.pk), "source.txt", content)
        source = DocumentSource.objects.create(task=task, original_name="source.txt",
            media_type="text/plain", path=path, sha256=checksum, size=len(content),
            parsed={"text": content.decode()}, uploaded_by=self.owner)
        payload = {"sources": [{"id": str(source.pk), "sha256": checksum}]}
        if retrieval:
            payload["retrieval"] = _authorization(task)
        DocumentRevision.objects.create(task=task, kind=DocumentRevision.Kind.INPUT,
            version=1, payload=payload, sha256=hashlib.sha256(b"input").hexdigest())
        if register:
            self._reference("document_source", source.pk, checksum, digest=checksum)
        return task, source

    def _assert_denied(self, root=None):
        with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
            check_sources(root or self.root)

    def _attachment(self, *, owner=None):
        owner = owner or self.owner
        content = b"private attachment"
        _, path, checksum = write_source(f"agent/{owner.pk}", "private.txt", content)
        item = AgentAttachment.objects.create(owner=owner, name="private.txt",
            content_type="text/plain", size=len(content), path=path, sha256=checksum)
        self.message.attachment_references = [{"type": "agent_attachment",
            "id": str(item.pk), "sha256": checksum}]
        self.message.save(update_fields=["attachment_references"])
        return item

    def _resume_batch(self):
        self._set_department("hr", "hr")
        request = RecruitmentRequest.objects.create(created_by=self.owner, updated_by=self.owner,
                                                    position_name="synthetic role")
        jd = JDVersion.objects.create(request=request, version=1, input_version=1, body="synthetic JD",
                                      requirements={}, created_by=self.owner)
        batch = ResumeScreeningBatch.objects.create(jd_version=jd, created_by=self.owner,
            idempotency_key=str(uuid.uuid4()), input_version=1, agent_root_id=str(self.root.pk),
            agent_work_id=str(self.work.pk), agent_requirement_version=1)
        content = b"synthetic resume"
        saved = save_file("resume.txt", content)
        artifact = ResumeArtifact.objects.create(batch=batch, file_id=saved["file_id"],
            filename=saved["filename"], sha256=saved["sha256"], size=saved["size"],
            uploaded_by=self.owner)
        self._reference("resume_batch", batch.pk, batch.version)
        return request, batch, artifact

    def _resume_artifact_reference(self, *, register_batch=False, revision=None, digest=None):
        request, batch, artifact = self._resume_batch()
        if not register_batch:
            AgentBusinessReference.objects.filter(root=self.root,
                domain_type="resume_batch").delete()
        self._reference("resume_artifact", artifact.pk,
            artifact.version if revision is None else revision,
            digest=artifact.sha256 if digest is None else digest)
        return request, batch, artifact

    def test_revoked_retrieval_stays_denied_after_new_requirement_drops_old_source(self):
        authorization = {self.owner.pk: {"dataset-a": ["document-a"]}}
        with override_settings(PRODUCT_RETRIEVAL_AUTHORIZATIONS=authorization):
            task, _ = self._product_source(retrieval=True)
            check_sources(self.root)
            newer_message = AgentMessage.objects.create(conversation=self.root.conversation,
                work=self.work, role="user", content="new requirement")
            AgentRequirement.objects.create(work=self.work, version=2,
                user_message=newer_message, content="new requirement", source_versions=[])
            self.work.current_requirement_version = 2
            self.work.save(update_fields=["current_requirement_version"])
            DocumentRevision.objects.create(task=task, kind=DocumentRevision.Kind.INPUT,
                version=2, payload={"sources": []}, sha256=hashlib.sha256(b"new input").hexdigest())
            task.input_version = 2
            task.version += 1
            task.save(update_fields=["input_version", "version"])
        with override_settings(PRODUCT_RETRIEVAL_AUTHORIZATIONS={
                self.owner.pk: {"dataset-a": ["different-document"]}}):
            self._assert_denied()

    def test_revoked_root_associated_product_source_is_denied_without_reference_row(self):
        authorization = {self.owner.pk: {"dataset-a": ["document-a"]}}
        with override_settings(PRODUCT_RETRIEVAL_AUTHORIZATIONS=authorization):
            self._product_source(retrieval=True, register=False)
            check_sources(self.root)
        with override_settings(PRODUCT_RETRIEVAL_AUTHORIZATIONS={
                self.owner.pk: {"dataset-a": ["different-document"]}}):
            self._assert_denied()

    def test_revoked_retrieval_without_document_source_stays_denied_after_input_advances(self):
        authorization = {self.owner.pk: {"dataset-a": ["document-a"]}}
        with override_settings(PRODUCT_RETRIEVAL_AUTHORIZATIONS=authorization):
            task = DocumentTask.objects.create(owner=self.owner, title="retrieval-only",
                idempotency_key=str(uuid.uuid4()), payload_hash="a" * 64, input_version=1,
                agent_root_id=str(self.root.pk), agent_work_id=str(self.work.pk),
                agent_requirement_version=1)
            DocumentRevision.objects.create(task=task, kind=DocumentRevision.Kind.INPUT,
                version=1, payload={"retrieval": _authorization(task)},
                sha256=hashlib.sha256(b"retrieval input").hexdigest())
            self._reference("document_task", task.pk, task.version)
            check_sources(self.root)

            DocumentRevision.objects.create(task=task, kind=DocumentRevision.Kind.INPUT,
                version=2, payload={"sources": []},
                sha256=hashlib.sha256(b"new retrieval input").hexdigest())
            task.input_version = 2
            task.version += 1
            task.save(update_fields=["input_version", "version"])
            check_sources(self.root)

        with override_settings(PRODUCT_RETRIEVAL_AUTHORIZATIONS={
                self.owner.pk: {"dataset-a": ["different-document"]}}):
            self._assert_denied()

    def test_new_authorized_source_snapshot_cannot_cover_old_revoked_snapshot(self):
        original = {self.owner.pk: {"dataset-a": ["document-a"]}}
        replacement = {self.owner.pk: {"dataset-a": ["document-b"]}}
        with override_settings(PRODUCT_RETRIEVAL_AUTHORIZATIONS=original):
            task, source = self._product_source(retrieval=True)
            check_sources(self.root)

        with override_settings(PRODUCT_RETRIEVAL_AUTHORIZATIONS=replacement):
            DocumentRevision.objects.create(task=task, kind=DocumentRevision.Kind.INPUT,
                version=2, payload={"sources": [{"id": str(source.pk), "sha256": source.sha256}],
                                    "retrieval": _authorization(task)},
                sha256=hashlib.sha256(b"replacement input").hexdigest())
            task.input_version = 2
            task.version += 1
            task.save(update_fields=["input_version", "version"])
            self._assert_denied()

    def test_deleted_product_source_row_or_file_denies_loaded_reference(self):
        _, source = self._product_source()
        check_sources(self.root)
        source.delete()
        self._assert_denied()

    def test_deleted_product_source_from_root_input_denies_without_reference_row(self):
        _, source = self._product_source(register=False)
        check_sources(self.root)
        source.delete()
        self._assert_denied()

    def test_product_source_file_hash_is_rechecked(self):
        _, source = self._product_source()
        check_sources(self.root)
        resolve_relative(source.path).write_bytes(b"replaced source")
        self._assert_denied()

    def test_product_task_version_progress_does_not_revoke_source(self):
        task, source = self._product_source()
        previous = task.revisions.get(kind=DocumentRevision.Kind.INPUT, version=1)
        DocumentRevision.objects.create(task=task, kind=DocumentRevision.Kind.INPUT,
            version=2, payload=previous.payload,
            sha256=hashlib.sha256(b"normal input progress").hexdigest())
        task.input_version = 2
        task.version += 1
        task.save(update_fields=["input_version", "version"])
        self.assertEqual(source.task_id, task.pk)
        check_sources(self.root)

    def test_product_artifact_hash_is_rechecked(self):
        task = DocumentTask.objects.create(owner=self.owner, title="output",
            idempotency_key=str(uuid.uuid4()), payload_hash="b" * 64,
            input_version=1, agent_root_id=str(self.root.pk), agent_work_id=str(self.work.pk))
        DocumentRevision.objects.create(task=task, kind=DocumentRevision.Kind.INPUT,
            version=1, payload={"sources": []}, sha256="d" * 64)
        content = b"synthetic artifact"
        _, path, checksum = write_source(str(task.pk), "output.txt", content)
        artifact = DocumentArtifact.objects.create(task=task, version=1, path=path,
            sha256=checksum, blueprint_hash="c" * 64, input_hash="d" * 64,
            template_hash="e" * 64)
        self._reference("document_artifact", artifact.pk, artifact.version, digest=checksum)
        check_sources(self.root)
        resolve_relative(path).write_bytes(b"tampered artifact")
        self._assert_denied()

    def test_attachment_delete_marker_denies_loaded_message(self):
        item = self._attachment()
        check_sources(self.root)
        item.deleted_at = timezone.now()
        item.save(update_fields=["deleted_at"])
        self._assert_denied()

    def test_attachment_file_digest_is_rechecked(self):
        item = self._attachment()
        check_sources(self.root)
        resolve_relative(item.path).write_bytes(b"changed attachment")
        self._assert_denied()

    def test_attachment_owned_by_another_user_is_denied(self):
        foreign = self.create_user("foreign-source-owner")
        item = self._attachment(owner=foreign)
        self._assert_denied()

    def test_hr_manual_archive_expiration_and_delete_marker_deny_loaded_batch(self):
        request, batch, artifact = self._resume_batch()
        old = timezone.now() - timedelta(days=3650)
        RecruitmentRequest.objects.filter(pk=request.pk).update(created_at=old)
        ResumeScreeningBatch.objects.filter(pk=batch.pk).update(created_at=old)
        check_sources(self.root)

        artifact.archive_state = "deleted"
        artifact.save(update_fields=["archive_state"])
        self._assert_denied()
        artifact.archive_state = "active"
        artifact.save(update_fields=["archive_state"])

        batch.archive_state = "deleted"
        batch.save(update_fields=["archive_state"])
        self._assert_denied()
        batch.archive_state = "active"
        batch.save(update_fields=["archive_state"])

        request.archive_state = "expired"
        request.save(update_fields=["archive_state"])
        self._assert_denied()
        request.archive_state = "active"
        request.save(update_fields=["archive_state"])

        root = Path(self.tempdir.name) / "hr" / artifact.file_id
        root.with_suffix(".delete").touch()
        self._assert_denied()

    def test_resume_artifact_reference_checks_exact_version_even_with_valid_batch_alias(self):
        _, _, artifact = self._resume_artifact_reference(
            register_batch=True, revision=2, digest=None)
        self._assert_denied()
        self.assertEqual(artifact.version, 1)

    def test_resume_artifact_reference_checks_exact_digest_even_with_valid_batch_alias(self):
        self._resume_artifact_reference(register_batch=True, digest="a" * 64)
        self._assert_denied()

    def test_resume_artifact_requires_owned_objects_and_root_work_binding(self):
        request, batch, artifact = self._resume_artifact_reference()
        foreign = self.create_user("foreign-resume-owner")

        batch.agent_root_id = str(uuid.uuid4())
        batch.save(update_fields=["agent_root_id"])
        self._assert_denied()
        batch.agent_root_id = str(self.root.pk)
        batch.save(update_fields=["agent_root_id"])

        batch.agent_work_id = str(uuid.uuid4())
        batch.save(update_fields=["agent_work_id"])
        self._assert_denied()
        batch.agent_work_id = str(self.work.pk)
        batch.save(update_fields=["agent_work_id"])

        artifact.uploaded_by = foreign
        artifact.save(update_fields=["uploaded_by"])
        self._assert_denied()
        artifact.uploaded_by = self.owner
        artifact.save(update_fields=["uploaded_by"])

        batch.created_by = foreign
        batch.save(update_fields=["created_by"])
        self._assert_denied()
        batch.created_by = self.owner
        batch.save(update_fields=["created_by"])

        batch.jd_version.created_by = foreign
        batch.jd_version.save(update_fields=["created_by"])
        self._assert_denied()
        batch.jd_version.created_by = self.owner
        batch.jd_version.save(update_fields=["created_by"])

        request.created_by = foreign
        request.save(update_fields=["created_by"])
        self._assert_denied()

        request.created_by = self.owner
        request.save(update_fields=["created_by"])
        self.owner.department_code = "product"
        self.owner.save(update_fields=["department_code"])
        self._assert_denied()

    def test_resume_artifact_rechecks_active_state_delete_marker_and_file_hash(self):
        request, batch, artifact = self._resume_artifact_reference()

        artifact.archive_state = "deleted"
        artifact.save(update_fields=["archive_state"])
        self._assert_denied()
        artifact.archive_state = "active"
        artifact.save(update_fields=["archive_state"])

        batch.archive_state = "deleted"
        batch.save(update_fields=["archive_state"])
        self._assert_denied()
        batch.archive_state = "active"
        batch.save(update_fields=["archive_state"])

        request.archive_state = "expired"
        request.save(update_fields=["archive_state"])
        self._assert_denied()
        request.archive_state = "active"
        request.save(update_fields=["archive_state"])

        source_path = Path(self.tempdir.name) / "hr" / artifact.file_id
        source_path.with_suffix(".delete").touch()
        self._assert_denied()
        source_path.with_suffix(".delete").unlink()
        source_path.write_bytes(b"tampered synthetic resume")
        self._assert_denied()

    def test_old_active_hr_archive_remains_authorized(self):
        request, batch, _ = self._resume_batch()
        old = timezone.now() - timedelta(days=3650)
        RecruitmentRequest.objects.filter(pk=request.pk).update(created_at=old)
        ResumeScreeningBatch.objects.filter(pk=batch.pk).update(created_at=old)
        check_sources(self.root)

    def test_finance_record_requires_current_grant_and_own_undeleted_row(self):
        self._set_department("finance")
        BusinessLedgerGrant.objects.create(user=self.owner, department="finance", can_edit=True)
        row = {"project_id": "FIN-1", "project_name": "own draft"}
        metadata_id = str(uuid.uuid4())
        workbook = BusinessLedgerWorkbook.objects.create(department="finance", revision=1,
            records=[row], record_meta={metadata_id: {"project_id": row["project_id"],
                "record_author_id": self.owner.pk, "draft_actor_id": self.owner.pk,
                "draft_revision": 1, "draft_checksum": _row_checksum(row), "deleted": False}},
            created_by=self.owner, updated_by=self.owner)
        self._reference("finance_record", row["project_id"], workbook.revision)
        check_sources(self.root)
        BusinessLedgerGrant.objects.filter(user=self.owner, department="finance").delete()
        self._assert_denied()
        BusinessLedgerGrant.objects.create(user=self.owner, department="finance", can_edit=True)
        check_sources(self.root)
        workbook.record_meta[metadata_id]["deleted"] = True
        workbook.records = []
        workbook.save(update_fields=["record_meta", "records"])
        self._assert_denied()

    def test_general_manager_cannot_read_employee_private_finance_draft(self):
        self._set_department("finance")
        employee = self.owner
        BusinessLedgerGrant.objects.create(user=employee, department="finance", can_edit=True)
        row = {"project_id": "FIN-2", "project_name": "employee draft"}
        metadata_id = str(uuid.uuid4())
        BusinessLedgerWorkbook.objects.create(department="finance", revision=1, records=[row],
            record_meta={metadata_id: {"project_id": row["project_id"],
                "record_author_id": employee.pk, "draft_actor_id": employee.pk,
                "draft_revision": 1, "draft_checksum": _row_checksum(row), "deleted": False}},
            created_by=employee, updated_by=employee)
        manager = self.create_user("source-manager", "general_manager")
        manager_root, manager_work, _, manager_requirement = self._new_scope(manager, "")
        self._reference("finance_record", row["project_id"], 1, root=manager_root,
                        work=manager_work, requirement=manager_requirement)
        self._assert_denied(manager_root)

    def test_manager_business_revision_uses_published_read_authorization(self):
        manager = self.create_user("business-manager", "general_manager")
        employee = self.create_user("business-employee")
        workbook = BusinessLedgerWorkbook.objects.create(department="finance",
            created_by=employee, updated_by=employee)
        revision = BusinessLedgerRevision.objects.create(workbook=workbook, revision=1,
            state=BusinessLedgerWorkbook.State.PUBLISHED, source_name="synthetic",
            records=[], record_meta={}, checksum="f" * 64, action="publish", actor=employee)
        root, work, _, requirement = self._new_scope(manager, "")
        self._reference("business_revision", revision.pk, revision.revision,
            digest=revision.checksum, root=root, work=work, requirement=requirement)
        check_sources(root)
        revision.state = BusinessLedgerWorkbook.State.DRAFT
        revision.save(update_fields=["state"])
        self._assert_denied(root)

    def test_finance_employee_can_reference_only_own_published_business_revision(self):
        self._set_department("finance")
        grant = BusinessLedgerGrant.objects.create(user=self.owner, department="finance", can_edit=True)
        records = [{"project_id": "FIN-PUBLISHED", "project_name": "published synthetic"}]
        workbook = BusinessLedgerWorkbook.objects.create(department="finance",
            state=BusinessLedgerWorkbook.State.PUBLISHED, revision=1, records=records,
            record_meta={}, created_by=self.owner, updated_by=self.owner,
            published_by=self.owner)
        checksum = _workbook_checksum(workbook)
        revision = BusinessLedgerRevision.objects.create(workbook=workbook, revision=1,
            state=BusinessLedgerWorkbook.State.PUBLISHED, source_name=workbook.source_name,
            as_of=workbook.as_of, records=records, record_meta={}, checksum=checksum,
            action="publish", actor=self.owner)
        self._reference("business_revision", revision.pk, revision.revision, digest=checksum)
        check_sources(self.root)

        revision.state = BusinessLedgerWorkbook.State.DRAFT
        revision.save(update_fields=["state"])
        self._assert_denied()
        revision.state = BusinessLedgerWorkbook.State.PUBLISHED
        revision.save(update_fields=["state"])

        foreign = self.create_user("foreign-finance-publisher")
        revision.actor = foreign
        revision.save(update_fields=["actor"])
        self._assert_denied()
        revision.actor = self.owner
        revision.save(update_fields=["actor"])

        reference = AgentBusinessReference.objects.get(root=self.root, domain_type="business_revision")
        reference.digest = "a" * 64
        reference.save(update_fields=["digest"])
        self._assert_denied()
        reference.digest = checksum
        reference.save(update_fields=["digest"])

        revision.records = [{"project_id": "FIN-TAMPERED"}]
        revision.save(update_fields=["records"])
        self._assert_denied()

        revision.records = records
        revision.save(update_fields=["records"])
        grant.delete()
        self._assert_denied()

    def test_finance_published_revision_is_not_readable_from_other_department_root(self):
        workbook = BusinessLedgerWorkbook.objects.create(department="finance",
            state=BusinessLedgerWorkbook.State.PUBLISHED, revision=1,
            created_by=self.owner, updated_by=self.owner)
        checksum = _workbook_checksum(workbook)
        revision = BusinessLedgerRevision.objects.create(workbook=workbook, revision=1,
            state=BusinessLedgerWorkbook.State.PUBLISHED, source_name=workbook.source_name,
            records=[], record_meta={}, checksum=checksum, action="publish", actor=self.owner)
        self._reference("business_revision", revision.pk, revision.revision, digest=checksum)
        self._assert_denied()

    def test_unknown_registered_domain_type_fails_closed(self):
        self._reference("unknown_source", "opaque-id", "v1")
        self._assert_denied()
