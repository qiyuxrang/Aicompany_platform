from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase, override_settings
from django.urls import include, path

from portal.agent_models import (AgentAttachment, AgentBusinessReference, AgentConversation,
                                 AgentMessage, AgentRequirement, AgentRun, AgentWorkTask)
from portal.business_boards import _checksum
from portal.business_models import BusinessLedgerRevision, BusinessLedgerWorkbook
from portal.hr_recruitment_models import JDVersion, RecruitmentRequest
from portal.hr_resume_storage import save_file
from portal.hr_screening_models import ResumeArtifact, ResumeScreeningBatch
from portal.models import Module, Role, User
from portal.product_models import DocumentArtifact, DocumentRevision, DocumentSource, DocumentTask
from portal.product_storage import write_source


urlpatterns = [path("api/agent/", include("portal.agent_management"))]


@override_settings(ROOT_URLCONF=__name__, AGENT_PLATFORM_ENABLED=True, HTTPS=False)
class AgentManagementTests(TestCase):
    def setUp(self):
        self.temp_storage = TemporaryDirectory()
        self.addCleanup(self.temp_storage.cleanup)
        storage_settings = override_settings(PRODUCT_STORAGE_ROOT=Path(self.temp_storage.name),
                                             HR_STORAGE_ROOT=Path(self.temp_storage.name))
        storage_settings.enable()
        self.addCleanup(storage_settings.disable)

        business = Module.objects.create(code="business", name="经营看板", enabled=True)
        manager_role = Role.objects.create(code="general_manager", name="总经理")
        manager_role.modules.add(business)
        self.manager = self._user("manager")
        self.manager.roles.add(manager_role)
        self.employee = self._user("employee", department_code="product")
        self._login(self.manager)
        self.conversation, self.work, self.requirement, self.root = self._work(self.employee)

    def _login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["version"] = user.session_version
        session.save()

    def _user(self, username, **fields):
        user = User.objects.create_user(username=username, password="synthetic-password")
        user.is_active = True
        user.must_change_password = False
        for name, value in fields.items():
            setattr(user, name, value)
        user.save()
        return user

    def _work(self, owner):
        conversation = AgentConversation.objects.create(owner=owner, department_code="product")
        message = AgentMessage.objects.create(conversation=conversation, role="user",
                                              content="PRIVATE EMPLOYEE CHAT CONTENT")
        work = AgentWorkTask.objects.create(owner=owner, department_code="product",
                                            conversation=conversation, goal=message.content)
        message.work = work
        message.save(update_fields=["work"])
        requirement = AgentRequirement.objects.create(work=work, version=1, user_message=message,
                                                     content=message.content)
        root = AgentRun.objects.create(id=conversation.root_run_id, root_run_id=conversation.root_run_id,
                                       conversation=conversation, work=work, requirement=requirement)
        return conversation, work, requirement, root

    def _reference(self, domain_type, object_id, revision, *, work=None, root=None, requirement=None):
        return AgentBusinessReference.objects.create(
            root=root or self.root, work=work or self.work, requirement=requirement or self.requirement,
            domain_type=domain_type, object_id=str(object_id), revision=str(revision),
            digest="operation-argument-digest", operation_key=f"{domain_type}:{uuid4()}",
            public_summary="Registered business summary",
        )

    def _product_task(self, *, owner=None):
        task = DocumentTask.objects.create(
            owner=owner or self.employee, title="Approved product task", idempotency_key=str(uuid4()),
            payload_hash="a" * 64, agent_root_id=str(self.root.pk), agent_work_id=str(self.work.pk),
            agent_requirement_version=self.requirement.version,
        )
        revision = DocumentRevision.objects.create(
            task=task, kind=DocumentRevision.Kind.INPUT, version=1,
            payload={"project": task.title, "requirements": "business scope", "background": "",
                     "conditions": [], "items": [], "authorization_dependencies": []},
            sha256="b" * 64, created_by=task.owner,
        )
        task.input_version = revision.version
        task.save(update_fields=["input_version"])
        return task

    def test_manager_reads_registered_business_and_downloads_exact_source(self):
        task = self._product_task()
        task_reference = self._reference("document_task", task.pk, task.version)
        response = self.client.get(f"/api/agent/management/references/{task_reference.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["title"], task.title)
        self.assertNotIn("PRIVATE EMPLOYEE CHAT CONTENT", response.content.decode())
        self.assertNotIn("messages", response.json())

        content = b"original approved business source"
        source_id, relative, checksum = write_source(task.pk, "source.txt", content)
        source = DocumentSource.objects.create(task=task, id=source_id, original_name="source.txt",
                                               media_type="text/plain", path=relative, sha256=checksum,
                                               size=len(content), parsed={"background": content.decode()},
                                               uploaded_by=self.employee)
        source_reference = self._reference("document_source", source.pk, checksum)
        downloaded = self.client.get(f"/api/agent/management/references/{source_reference.pk}/download/")
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(b"".join(downloaded.streaming_content), content)

    def test_completed_work_reference_survives_next_work_on_same_technical_root(self):
        task = self._product_task()
        reference = self._reference("document_task", task.pk, task.version)
        self.work.state = "completed"
        self.work.save(update_fields=["state"])
        next_work = AgentWorkTask.objects.create(owner=self.employee, department_code="product",
            conversation=self.conversation, goal="PRIVATE NEXT WORK", state="running")
        message = AgentMessage.objects.create(conversation=self.conversation, work=next_work,
                                             role="user", content=next_work.goal)
        next_requirement = AgentRequirement.objects.create(work=next_work, version=1,
            user_message=message, content=message.content)
        self.root.work = next_work
        self.root.requirement = next_requirement
        self.root.save(update_fields=["work", "requirement"])
        response = self.client.get(f"/api/agent/management/references/{reference.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["object_id"], str(task.pk))
        self.assertNotContains(response, "PRIVATE NEXT WORK")

    def test_non_gm_is_denied(self):
        self._login(self.employee)
        response = self.client.get(f"/api/agent/management/references/{uuid4()}/")
        self.assertEqual(response.status_code, 403)

    def test_admin_is_denied_even_with_manager_role(self):
        for field in ("is_staff", "is_superuser"):
            setattr(self.manager, field, True)
            self.manager.save(update_fields=[field])
            response = self.client.get(f"/api/agent/management/references/{uuid4()}/")
            self.assertEqual(response.status_code, 403)
            setattr(self.manager, field, False)
            self.manager.save(update_fields=[field])

        admin_role = Role.objects.create(code="platform_admin", name="平台管理员")
        self.manager.roles.add(admin_role)
        response = self.client.get(f"/api/agent/management/references/{uuid4()}/")
        self.assertEqual(response.status_code, 403)

    def test_revoked_business_module_is_denied_on_each_request(self):
        Module.objects.filter(code="business").update(enabled=False)
        response = self.client.get(f"/api/agent/management/references/{uuid4()}/")
        self.assertEqual(response.status_code, 403)

    def test_domain_owner_must_match_work_owner(self):
        task = self._product_task(owner=self._user("other_employee", department_code="product"))
        reference = self._reference("document_task", task.pk, task.version)
        response = self.client.get(f"/api/agent/management/references/{reference.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_stale_domain_version_is_rejected(self):
        task = self._product_task()
        reference = self._reference("document_task", task.pk, task.version)
        DocumentTask.objects.filter(pk=task.pk).update(version=task.version + 1)
        response = self.client.get(f"/api/agent/management/references/{reference.pk}/")
        self.assertEqual(response.status_code, 409)

    def test_private_attachment_reference_is_not_a_business_source(self):
        attachment = AgentAttachment.objects.create(
            owner=self.employee, name="private.txt", content_type="text/plain", size=0,
            path="private/opaque", sha256="c" * 64, source_scope="private",
        )
        reference = self._reference("private_attachment", attachment.pk, "1")
        response = self.client.get(f"/api/agent/management/references/{reference.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_path_like_object_id_is_not_treated_as_a_filesystem_path(self):
        reference = self._reference("document_source", r"C:\private\resume.pdf", "a" * 64)
        response = self.client.get(f"/api/agent/management/references/{reference.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_archived_hr_batch_cannot_be_reopened(self):
        request = RecruitmentRequest.objects.create(
            position_name="Archived role", created_by=self.employee, updated_by=self.employee,
            archive_state="expired",
        )
        jd = JDVersion.objects.create(request=request, version=1, input_version=1,
                                      body="Archived JD", created_by=self.employee)
        batch = ResumeScreeningBatch.objects.create(
            jd_version=jd, created_by=self.employee, idempotency_key="archived-batch",
            input_version=1, requirements={}, archive_state="expired",
        )
        reference = self._reference("resume_batch", batch.pk, batch.version)
        response = self.client.get(f"/api/agent/management/references/{reference.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_hr_resume_deletion_marker_blocks_download(self):
        request = RecruitmentRequest.objects.create(
            position_name="Active role", created_by=self.employee, updated_by=self.employee,
        )
        jd = JDVersion.objects.create(request=request, version=1, input_version=1,
                                      body="Active JD", created_by=self.employee)
        batch = ResumeScreeningBatch.objects.create(
            jd_version=jd, created_by=self.employee, idempotency_key="deleted-file-batch",
            input_version=1, requirements={},
        )
        artifact = ResumeArtifact.objects.create(
            batch=batch, uploaded_by=self.employee,
            **save_file("candidate.txt", b"synthetic resume source"),
        )
        (Path(self.temp_storage.name) / f"{artifact.file_id}.delete").write_bytes(b"")
        reference = self._reference("resume_artifact", artifact.pk, artifact.version)
        response = self.client.get(f"/api/agent/management/references/{reference.pk}/download/")
        self.assertEqual(response.status_code, 409)

    def test_registered_product_artifact_download_checks_its_hash_and_source_auth(self):
        task = self._product_task()
        content = b"generated business deliverable"
        artifact_id, relative, checksum = write_source(task.pk, "result.docx", content)
        artifact = DocumentArtifact.objects.create(
            id=artifact_id, task=task, version=1, path=relative, sha256=checksum,
            family="technical-solution", blueprint_hash="d" * 64,
            input_hash="b" * 64, template_hash="e" * 64,
        )
        reference = self._reference("document_artifact", artifact.pk, artifact.version)
        self.assertNotEqual(reference.digest, artifact.sha256)
        response = self.client.get(f"/api/agent/management/references/{reference.pk}/download/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), content)

    def test_tampered_product_artifact_fails_its_domain_hash(self):
        task = self._product_task()
        content = b"verified deliverable"
        artifact_id, relative, checksum = write_source(task.pk, "result.docx", content)
        artifact = DocumentArtifact.objects.create(
            id=artifact_id, task=task, version=1, path=relative, sha256=checksum,
            family="technical-solution", blueprint_hash="d" * 64,
            input_hash="b" * 64, template_hash="e" * 64,
        )
        reference = self._reference("document_artifact", artifact.pk, artifact.version)
        (Path(self.temp_storage.name) / relative).write_bytes(b"tampered deliverable")
        response = self.client.get(f"/api/agent/management/references/{reference.pk}/download/")
        self.assertEqual(response.status_code, 409)

    def test_revoked_product_source_authorization_blocks_artifact(self):
        task = self._product_task()
        revision = task.revisions.get(kind=DocumentRevision.Kind.INPUT, version=task.input_version)
        revision.payload["authorization_dependencies"] = [{"scope_hash": "revoked"}]
        revision.save(update_fields=["payload"])
        content = b"previously authorized deliverable"
        artifact_id, relative, checksum = write_source(task.pk, "result.docx", content)
        artifact = DocumentArtifact.objects.create(
            id=artifact_id, task=task, version=1, path=relative, sha256=checksum,
            family="technical-solution", blueprint_hash="d" * 64,
            input_hash=revision.sha256, template_hash="e" * 64,
        )
        reference = self._reference("document_artifact", artifact.pk, artifact.version)
        with patch("portal.product_retrieval.authorization_current", return_value={"current": False}):
            response = self.client.get(f"/api/agent/management/references/{reference.pk}/download/")
        self.assertEqual(response.status_code, 409)

    def test_active_hr_resume_is_downloaded_from_verified_archive(self):
        request = RecruitmentRequest.objects.create(
            position_name="Approved role", created_by=self.employee, updated_by=self.employee,
        )
        jd = JDVersion.objects.create(request=request, version=1, input_version=1,
                                      body="Approved JD", created_by=self.employee)
        batch = ResumeScreeningBatch.objects.create(
            jd_version=jd, created_by=self.employee, idempotency_key="active-batch",
            input_version=1, requirements={"required": ["Python"]},
        )
        content = b"synthetic resume source"
        artifact = ResumeArtifact.objects.create(
            batch=batch, uploaded_by=self.employee,
            **save_file("candidate.txt", content),
        )
        reference = self._reference("resume_artifact", artifact.pk, artifact.version)
        response = self.client.get(f"/api/agent/management/references/{reference.pk}/download/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), content)

    def test_gm_can_read_an_exact_published_finance_revision(self):
        records = [{"project_id": "P-1", "amount": "12500.00", "supplier": "供应商甲"}]
        workbook = BusinessLedgerWorkbook.objects.create(
            department="finance", state=BusinessLedgerWorkbook.State.PUBLISHED,
            revision=1, as_of=date(2026, 9, 30), records=records,
            created_by=self.employee, updated_by=self.employee,
        )
        revision = BusinessLedgerRevision.objects.create(
            workbook=workbook, revision=1, state=BusinessLedgerWorkbook.State.PUBLISHED,
            source_name="finance.xlsx", as_of=workbook.as_of, records=records,
            record_meta={}, checksum=_checksum(workbook, records=records), action="publish",
            actor=self.employee,
        )
        reference = self._reference("business_revision", revision.pk, revision.revision)
        reference.digest = revision.checksum
        reference.save(update_fields=["digest"])
        response = self.client.get(f"/api/agent/management/references/{reference.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["records"], records)

    def test_unpublished_finance_revision_is_not_visible_to_gm(self):
        records = [{"project_id": "P-2", "amount": "50.00"}]
        workbook = BusinessLedgerWorkbook.objects.create(
            department="finance", state=BusinessLedgerWorkbook.State.DRAFT,
            revision=1, as_of=date(2026, 9, 30), records=records,
            created_by=self.employee, updated_by=self.employee,
        )
        revision = BusinessLedgerRevision.objects.create(
            workbook=workbook, revision=1, state=BusinessLedgerWorkbook.State.DRAFT,
            source_name="finance.xlsx", as_of=workbook.as_of, records=records,
            record_meta={}, checksum=_checksum(workbook, records=records), action="record_update",
            actor=self.employee,
        )
        reference = self._reference("business_revision", revision.pk, revision.revision)
        response = self.client.get(f"/api/agent/management/references/{reference.pk}/")
        self.assertEqual(response.status_code, 404)
