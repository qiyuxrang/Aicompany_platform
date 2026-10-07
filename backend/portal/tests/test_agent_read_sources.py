import hashlib
import tempfile
import uuid
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone

from portal.agent_models import (AgentBusinessReference, AgentConversation, AgentMessage,
                                 AgentRequirement, AgentRun, AgentWorkTask)
from portal.agent_read_sources import check_read_sources, read_sources
from portal.agent_runtime import AgentDenied, RuntimeGuard, initialize_root
from portal.agent_tools import AgentTools, tools_for_run
from portal.business_boards import _checksum
from portal.business_models import (BusinessLedgerGrant, BusinessLedgerRevision,
                                    BusinessLedgerWorkbook)
from portal.hr_recruitment_models import JDVersion, RecruitmentRequest
from portal.hr_resume_storage import _path, save_file
from portal.hr_screening_models import ResumeArtifact, ResumeScreeningBatch
from portal.models import User
from portal.product_models import DocumentArtifact, DocumentRevision, DocumentSource, DocumentTask
from portal.product_service import append_revision, digest

from .base import PortalTestCase


class AgentReadSourceTests(PortalTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        settings_override = override_settings(HR_STORAGE_ROOT=root / "hr",
                                              PRODUCT_STORAGE_ROOT=root / "product")
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        self.hr = self.create_user("read-source-hr", "hr")
        self.hr.department_code = "hr"
        self.hr.save(update_fields=["department_code"])
        self.manager = self.create_user("read-source-manager", "general_manager")
        self.employee = self.create_user("read-source-employee", "hr")
        self.employee.department_code = "hr"
        self.employee.save(update_fields=["department_code"])

    def _guard(self, owner, department):
        conversation = AgentConversation.objects.create(owner=owner, department_code=department)
        root = AgentRun.objects.create(id=conversation.root_run_id, root_run_id=conversation.root_run_id,
            conversation=conversation, deadline_at=timezone.now() + timedelta(minutes=5), state="running",
            policy={"max_actions": 30, "max_model_calls": 10, "max_tool_calls": 12,
                    "max_launches": 6, "max_concurrent": 6, "max_active_ms": 60000})
        return RuntimeGuard(initialize_root(root.pk, owner.pk))

    def _hr_data(self, *, count=1):
        request = RecruitmentRequest.objects.create(created_by=self.hr, updated_by=self.hr,
            position_name="Synthetic role", original_text="Synthetic request")
        jd = JDVersion.objects.create(request=request, version=1, input_version=1,
            body="Synthetic job description", requirements={}, created_by=self.hr)
        batch = ResumeScreeningBatch.objects.create(jd_version=jd, created_by=self.hr,
            idempotency_key=str(uuid.uuid4()), input_version=1, status="completed")
        artifacts = []
        for index in range(count):
            content = f"synthetic resume {index}".encode()
            saved = save_file(f"resume-{index}.txt", content)
            artifacts.append(ResumeArtifact.objects.create(batch=batch, file_id=saved["file_id"],
                filename=saved["filename"], sha256=saved["sha256"], size=saved["size"],
                uploaded_by=self.hr))
        return request, jd, batch, artifacts

    def _published_revision(self):
        workbook = BusinessLedgerWorkbook.objects.create(department="engineering",
            as_of=date.today(), created_by=self.employee, updated_by=self.employee)
        revision = BusinessLedgerRevision.objects.create(workbook=workbook, revision=1,
            state=BusinessLedgerWorkbook.State.PUBLISHED, source_name="Synthetic published",
            as_of=date.today(), records=[], record_meta={}, checksum="",
            action="publish", actor=self.employee)
        revision.checksum = _checksum(workbook, records=revision.records, state=revision.state)
        revision.save(update_fields=["checksum"])
        return revision

    def test_legacy_batch_reads_twenty_exact_artifacts_without_work_or_admission_reset(self):
        _, _, batch, artifacts = self._hr_data(count=22)
        old = timezone.now() - timedelta(days=90)
        ResumeScreeningBatch.objects.filter(pk=batch.pk).update(created_at=old)
        guard = self._guard(self.hr, "hr")
        tools = AgentTools(guard)
        root = AgentRun.objects.get(pk=guard.binding.root_id)
        before = (root.action_count, root.tool_count, root.launch_count, root.policy["identity"],
                  root.policy["fence"], root.deadline_at)

        result = tools.hr_read_batch(str(batch.pk), 0, "legacy-batch-page")

        self.assertEqual(len(result["results"]), 20)
        check_read_sources(root)
        sources = read_sources(root)
        self.assertEqual({item["artifact_id"] for item in sources if item["kind"] == "hr_resume"},
                         {item["id"] for item in result["results"]})
        self.assertNotIn(str(artifacts[20].pk), {item["artifact_id"] for item in sources
                                                if item["kind"] == "hr_resume"})
        root.refresh_from_db()
        self.assertEqual((root.action_count, root.tool_count, root.launch_count, root.policy["identity"],
                          root.policy["fence"], root.deadline_at), before)
        self.assertIsNone(root.work_id)

    def test_hr_archive_owner_and_missing_file_revocations_fail_closed(self):
        request, _, batch, artifacts = self._hr_data()
        guard = self._guard(self.hr, "hr")
        AgentTools(guard).hr_read_batch(str(batch.pk), 0, "legacy-batch")
        root = AgentRun.objects.get(pk=guard.binding.root_id)
        check_read_sources(root)

        artifacts[0].archive_state = "deleted"
        artifacts[0].save(update_fields=["archive_state"])
        with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
            check_read_sources(root)
        artifacts[0].archive_state = "active"
        artifacts[0].save(update_fields=["archive_state"])

        batch.archive_state = "deleted"
        batch.save(update_fields=["archive_state"])
        with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
            check_read_sources(root)
        batch.archive_state = "active"
        batch.save(update_fields=["archive_state"])

        request.archive_state = "legacy_expired"
        request.save(update_fields=["archive_state"])
        with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
            check_read_sources(root)
        request.archive_state = "active"
        request.save(update_fields=["archive_state"])

        other = self.create_user("read-source-other")
        batch.created_by = other
        batch.save(update_fields=["created_by"])
        with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
            check_read_sources(root)
        batch.created_by = self.hr
        batch.save(update_fields=["created_by"])

        other = self.employee
        artifacts[0].uploaded_by = other
        artifacts[0].save(update_fields=["uploaded_by"])
        with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
            check_read_sources(root)
        artifacts[0].uploaded_by = self.hr
        artifacts[0].save(update_fields=["uploaded_by"])

        moved = save_file("same-resume.txt", b"synthetic resume replacement")
        original_file_id = artifacts[0].file_id
        artifacts[0].file_id = moved["file_id"]
        artifacts[0].save(update_fields=["file_id"])
        self.assertTrue(_path(original_file_id).is_file())
        with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
            check_read_sources(root)
        artifacts[0].file_id = original_file_id
        artifacts[0].save(update_fields=["file_id"])

        original_sha = artifacts[0].sha256
        artifacts[0].sha256 = "f" * 64
        artifacts[0].save(update_fields=["sha256"])
        self.assertTrue(_path(original_file_id).is_file())
        with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
            check_read_sources(root)
        artifacts[0].sha256 = original_sha
        artifacts[0].save(update_fields=["sha256"])

        request.created_by = other
        request.save(update_fields=["created_by"])
        with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
            check_read_sources(root)
        request.created_by = self.hr
        request.save(update_fields=["created_by"])

        _path(artifacts[0].file_id).unlink()
        with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
            check_read_sources(root)

    def test_resume_profile_version_can_advance_without_changing_file(self):
        _, _, batch, artifacts = self._hr_data()
        guard = self._guard(self.hr, "hr")
        AgentTools(guard).hr_read_batch(str(batch.pk), 0, "profile-v1")
        root = AgentRun.objects.get(pk=guard.binding.root_id)
        artifacts[0].profile = {"summary": "new derived profile"}
        artifacts[0].version += 1
        artifacts[0].save(update_fields=["profile", "version"])
        check_read_sources(root)

    def test_active_ninety_day_request_and_legal_version_progress_remain_readable(self):
        request, jd, batch, _ = self._hr_data(count=0)
        old = timezone.now() - timedelta(days=90)
        RecruitmentRequest.objects.filter(pk=request.pk).update(created_at=old)
        guard = self._guard(self.hr, "hr")
        tools = AgentTools(guard)
        tools.hr_read_jd(str(request.pk), "jd-v1")
        root = AgentRun.objects.get(pk=guard.binding.root_id)
        check_read_sources(root)

        request.input_version = 2
        request.position_name = "Updated synthetic role"
        request.save(update_fields=["input_version", "position_name", "updated_at"])
        newer = JDVersion.objects.create(request=request, version=2, input_version=2,
            body="Updated synthetic job description", requirements={}, created_by=self.hr)
        batch.jd_version = newer
        batch.input_version = 2
        batch.save(update_fields=["jd_version", "input_version", "updated_at"])
        tools.hr_read_jd(str(request.pk), "jd-v2")
        check_read_sources(root)

        jd.delete()
        with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
            check_read_sources(root)

    def test_newer_jd_registration_does_not_hide_an_old_revoked_reference(self):
        request, jd, batch, _ = self._hr_data(count=0)
        guard = self._guard(self.hr, "hr")
        tools = AgentTools(guard)
        tools.hr_read_jd(str(request.pk), "first-read")
        request.input_version = 2
        request.save(update_fields=["input_version", "updated_at"])
        newer = JDVersion.objects.create(request=request, version=2, input_version=2,
            body="Newly authorized description", requirements={}, created_by=self.hr)
        batch.jd_version = newer
        batch.input_version = 2
        batch.save(update_fields=["jd_version", "input_version", "updated_at"])
        tools.hr_read_jd(str(request.pk), "second-read")
        root = AgentRun.objects.get(pk=guard.binding.root_id)
        self.assertEqual({source["jd_id"] for source in read_sources(root) if source["kind"] == "hr_jd"},
                         {str(jd.pk), str(newer.pk)})
        jd.delete()
        with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
            check_read_sources(root)

    def test_manager_board_requires_published_revision_and_current_role(self):
        revision = self._published_revision()
        guard = self._guard(self.manager, "")
        AgentTools(guard).gm_read_business("engineering", "published-board")
        root = AgentRun.objects.get(pk=guard.binding.root_id)
        check_read_sources(root)

        revision.state = BusinessLedgerWorkbook.State.DRAFT
        revision.save(update_fields=["state"])
        with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
            check_read_sources(root)
        revision.state = BusinessLedgerWorkbook.State.PUBLISHED
        revision.save(update_fields=["state"])
        self.manager.roles.clear()
        with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
            check_read_sources(root)

    def test_manager_work_list_filters_reference_types_and_read_rechecks_resolver(self):
        revision = self._published_revision()
        conversation = AgentConversation.objects.create(owner=self.employee, department_code="hr")
        work = AgentWorkTask.objects.create(owner=self.employee, department_code="hr",
            conversation=conversation, goal="private employee request", public_summary="safe summary",
            state="running", current_requirement_version=1)
        message = AgentMessage.objects.create(conversation=conversation, work=work,
            role="user", content="private message body")
        requirement = AgentRequirement.objects.create(work=work, version=1, user_message=message,
            content=message.content)
        source_root = AgentRun.objects.create(id=conversation.root_run_id,
            root_run_id=conversation.root_run_id, conversation=conversation,
            work=work, requirement=requirement, state="running")
        allowed_types = ("document_task", "document_source", "document_artifact", "resume_batch",
                         "resume_artifact", "business_revision")
        for index, domain_type in enumerate((*allowed_types, "finance_record", "hr_screening_batch")):
            AgentBusinessReference.objects.create(root=source_root, work=work, requirement=requirement,
                domain_type=domain_type, object_id=str(revision.pk), revision=str(revision.revision),
                digest=revision.checksum, operation_key=f"source-{index}", public_summary="safe reference")
        reference = AgentBusinessReference.objects.create(root=source_root, work=work,
            requirement=requirement, domain_type="business_revision", object_id=str(revision.pk),
            revision=str(revision.revision), digest=revision.checksum, operation_key="published-revision",
            public_summary="published ledger")

        guard = self._guard(self.manager, "")
        tools = AgentTools(guard)
        self.assertEqual({tool.name for tool in tools_for_run(guard)},
                         {"gm_read_business", "gm_list_work", "gm_read_reference"})
        listed = tools.gm_list_work("hr", 0, "list-hr")
        self.assertEqual(len(listed["items"]), 1)
        item = listed["items"][0]
        self.assertEqual(item["summary"], "safe summary")
        self.assertEqual({ref["domain_type"] for ref in item["business_references"]}, set(allowed_types))
        self.assertNotIn("goal", item)
        self.assertNotIn("private message body", repr(listed))

        result = tools.gm_read_reference(str(reference.pk), "read-reference")
        self.assertEqual(result["data"]["checksum"], revision.checksum)
        self.assertEqual(result["url"], f"/api/agent/management/references/{reference.pk}/")
        self.assertNotIn("download_url", result)
        root = AgentRun.objects.get(pk=guard.binding.root_id)
        check_read_sources(root)
        self.manager.roles.clear()
        with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
            check_read_sources(root)

    def test_forged_read_source_pointer_is_rejected(self):
        guard = self._guard(self.hr, "hr")
        root = AgentRun.objects.get(pk=guard.binding.root_id)
        root.policy["read_sources"] = [{"source": {"kind": "hr_request",
            "request_id": str(uuid.uuid4()), "version": 1, "body_sha256": "a" * 64},
            "signature": "forged"}]
        root.save(update_fields=["policy"])
        with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
            check_read_sources(root)

    def _product_task(self, owner):
        task = DocumentTask.objects.create(owner=owner, title="Legacy source task",
            idempotency_key=f"read-source-{uuid.uuid4()}", payload_hash="a" * 64)
        revision = append_revision(task, DocumentRevision.Kind.INPUT,
            {"project": "Synthetic", "requirements": "read only", "background": "",
             "conditions": [], "items": [], "sources": [], "issues": []}, actor=owner)
        task.input_version = revision.version
        task.save(update_fields=["input_version"])
        return task, revision

    def _product_tools(self):
        owner = self.create_user("read-source-product", "product")
        owner.department_code = "product"
        owner.save(update_fields=["department_code"])
        return owner, AgentTools(self._guard(owner, "product"))

    def test_unbound_product_reads_register_exact_sources_and_old_input_revocation(self):
        owner, tools = self._product_tools()
        task, first_input = self._product_task(owner)
        task_id = str(task.pk)
        details = tools.read_product_task(task_id, "legacy-task")
        self.assertEqual(details["input"]["project"], "Synthetic")

        content = b"legacy source bytes"
        product_root = Path(self.tempdir.name) / "product"
        product_root.mkdir(parents=True, exist_ok=True)
        (product_root / "source.txt").write_bytes(content)
        source = DocumentSource.objects.create(task=task, original_name="source.txt", purpose="background",
            media_type="text/plain", path="source.txt", sha256=hashlib.sha256(content).hexdigest(),
            size=len(content), parsed={"background": "old parsed data", "blocks": []}, uploaded_by=owner)
        source_result = tools.read_product_source(task_id, str(source.pk), 0, "legacy-source")
        self.assertEqual(source_result["text"], "old parsed data")
        matches = tools.search_product_sources(task_id, "parsed", "legacy-search")
        self.assertEqual([item["source_id"] for item in matches], [str(source.pk)])

        artifact_content = b"legacy output bytes"
        (product_root / "output.docx").write_bytes(artifact_content)
        artifact = DocumentArtifact.objects.create(task=task, version=1, path="output.docx",
            sha256=hashlib.sha256(artifact_content).hexdigest(),
            family="technical-solution", blueprint_hash="b" * 64, input_hash=first_input.sha256,
            template_hash="c" * 64)
        tools.list_product_outputs(task_id, "legacy-outputs")
        root = AgentRun.objects.get(pk=tools.root_run_id)
        check_read_sources(root)

        artifact.sha256 = "e" * 64
        artifact.save(update_fields=["sha256"])
        with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
            check_read_sources(root)
        artifact.sha256 = hashlib.sha256(artifact_content).hexdigest()
        artifact.save(update_fields=["sha256"])

        changed = dict(first_input.payload)
        changed["requirements"] = "revoked old input"
        first_input.payload = changed
        first_input.save(update_fields=["payload"])
        with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
            check_read_sources(root)

    def test_newer_product_input_does_not_mask_revocation_of_old_read_dependency(self):
        owner, tools = self._product_tools()
        task, old_input = self._product_task(owner)
        old_payload = {**old_input.payload, "authorization_dependencies": [{"scope_hash": "a" * 64}]}
        old_input.payload = old_payload
        old_input.sha256 = digest(old_payload)
        old_input.save(update_fields=["payload", "sha256"])
        with patch("portal.product_retrieval.authorization_current", return_value={"current": True}):
            tools.read_product_task(str(task.pk), "authorized-old-input")
            newer = append_revision(task, DocumentRevision.Kind.INPUT,
                {"project": "Synthetic v2", "requirements": "new", "background": "",
                 "conditions": [], "items": [], "sources": [], "issues": []}, actor=owner)
            task.input_version = newer.version
            task.version += 1
            task.save(update_fields=["input_version", "version"])
            tools.read_product_task(str(task.pk), "authorized-new-input")
        root = AgentRun.objects.get(pk=tools.root_run_id)
        with patch("portal.product_retrieval.authorization_current", return_value={"current": False}):
            with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
                check_read_sources(root)

    def test_product_readers_require_root_department_and_current_owner(self):
        owner, tools = self._product_tools()
        task, _ = self._product_task(owner)
        root = AgentRun.objects.get(pk=tools.root_run_id)
        tools.read_product_task(str(task.pk), "before-scope-change")
        root.conversation.department_code = "hr"
        root.conversation.save(update_fields=["department_code"])
        with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
            tools.read_product_task(str(task.pk), "wrong-root-dept")
        root.conversation.department_code = "product"
        root.conversation.save(update_fields=["department_code"])
        task.owner = self.employee
        task.save(update_fields=["owner"])
        with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
            check_read_sources(root)

    def test_product_source_checks_uploader_and_private_file(self):
        owner, tools = self._product_tools()
        task, _ = self._product_task(owner)
        product_root = Path(self.tempdir.name) / "product"
        product_root.mkdir(parents=True, exist_ok=True)
        content = b"owner-bound source"
        source_path = product_root / "private.txt"
        source_path.write_bytes(content)
        source = DocumentSource.objects.create(task=task, original_name="private.txt", purpose="background",
            media_type="text/plain", path="private.txt", sha256=hashlib.sha256(content).hexdigest(),
            size=len(content), parsed={"background": "owner-bound text"}, uploaded_by=owner)
        tools.read_product_source(str(task.pk), str(source.pk), 0, "read-private-source")
        root = AgentRun.objects.get(pk=tools.root_run_id)
        check_read_sources(root)

        source.uploaded_by = self.employee
        source.save(update_fields=["uploaded_by"])
        with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
            check_read_sources(root)
        source.uploaded_by = owner
        source.save(update_fields=["uploaded_by"])
        source_path.unlink()
        with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
            check_read_sources(root)

    def _finance_fixture(self):
        from portal.business_boards import _checksum, _row_checksum

        owner = self.create_user("read-source-finance", "finance")
        owner.department_code = "finance"
        owner.save(update_fields=["department_code"])
        BusinessLedgerGrant.objects.create(user=owner, department="finance", can_edit=True)
        record = {"project_id": "FIN-001", "project_name": "Synthetic finance draft",
                  "contract_amount": "100.00", "received_amount": "20.00", "due_date": ""}
        metadata = {"record-1": {"project_id": record["project_id"], "record_author_id": owner.pk,
                    "draft_actor_id": owner.pk, "draft_revision": 1,
                    "draft_checksum": _row_checksum(record), "deleted": False}}
        workbook = BusinessLedgerWorkbook.objects.create(department="finance", revision=1,
            records=[record], record_meta=metadata, created_by=owner, updated_by=owner)
        checksum = _checksum(workbook)
        snapshot = BusinessLedgerRevision.objects.create(workbook=workbook, revision=1,
            state=workbook.state, source_name=workbook.source_name, as_of=None, records=[record],
            record_meta=metadata, checksum=checksum, action="record_create", actor=owner)
        return owner, workbook, snapshot, record, metadata

    def test_finance_draft_old_pointer_denies_delete_or_owner_reassignment(self):
        owner, workbook, snapshot, _, metadata = self._finance_fixture()
        guard = self._guard(owner, "finance")
        AgentTools(guard).finance_read_drafts("read-own-draft")
        root = AgentRun.objects.get(pk=guard.binding.root_id)
        check_read_sources(root)

        deleted_meta = {key: dict(value) for key, value in metadata.items()}
        deleted_meta["record-1"]["deleted"] = True
        workbook.record_meta = deleted_meta
        workbook.save(update_fields=["record_meta"])
        with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
            check_read_sources(root)

        reassigned = {key: dict(value) for key, value in metadata.items()}
        reassigned["record-1"]["record_author_id"] = self.employee.pk
        workbook.record_meta = reassigned
        workbook.save(update_fields=["record_meta"])
        with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
            check_read_sources(root)

        workbook.record_meta = metadata
        workbook.save(update_fields=["record_meta"])
        BusinessLedgerGrant.objects.filter(user=owner, department="finance").delete()
        with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
            check_read_sources(root)

    def test_finance_own_record_version_progress_keeps_old_snapshot_authorized(self):
        from portal.business_boards import _checksum, _row_checksum

        owner, workbook, _, record, metadata = self._finance_fixture()
        guard = self._guard(owner, "finance")
        AgentTools(guard).finance_read_drafts("read-own-draft-v1")
        root = AgentRun.objects.get(pk=guard.binding.root_id)
        newer_record = {**record, "received_amount": "30.00"}
        newer_meta = {key: dict(value) for key, value in metadata.items()}
        newer_meta["record-1"].update(draft_revision=2, draft_checksum=_row_checksum(newer_record))
        workbook.records = [newer_record]
        workbook.record_meta = newer_meta
        workbook.revision = 2
        workbook.save(update_fields=["records", "record_meta", "revision"])
        BusinessLedgerRevision.objects.create(workbook=workbook, revision=2, state=workbook.state,
            source_name=workbook.source_name, as_of=None, records=[newer_record], record_meta=newer_meta,
            checksum=_checksum(workbook), action="record_update", actor=owner)
        check_read_sources(root)
