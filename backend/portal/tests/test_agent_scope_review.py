from datetime import date, timedelta
from types import SimpleNamespace

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from portal import agent_api, agent_management
from portal.agent_models import (AgentBusinessReference, AgentConversation, AgentMessage,
                                 AgentRequirement, AgentRun, AgentWorkTask)
from portal.agent_read_sources import check_read_sources, read_sources
from portal.agent_runtime import AgentDenied, RuntimeGuard, initialize_root
from portal.agent_tools import AgentTools
from portal.business_boards import _checksum
from portal.business_models import BusinessLedgerRevision, BusinessLedgerWorkbook
from portal.hr_recruitment_models import JDVersion, RecruitmentRequest

from .base import PortalTestCase


@override_settings(AGENT_PLATFORM_ENABLED=True)
class AgentScopeReviewTests(PortalTestCase):
    def _work(self, owner, conversation, goal, message_text, version=1, state="running"):
        work = AgentWorkTask.objects.create(owner=owner, department_code=conversation.department_code,
            conversation=conversation, goal=goal, public_summary="safe summary", state=state,
            current_requirement_version=version)
        message = AgentMessage.objects.create(conversation=conversation, work=work, role="user",
            content=message_text)
        requirement = AgentRequirement.objects.create(work=work, version=version,
            user_message=message, content=message_text)
        return work, requirement

    def test_manager_history_stays_read_only_when_a_new_work_reuses_the_root(self):
        owner = self.create_user("scope-review-finance", "finance")
        owner.department_code = "finance"
        owner.save(update_fields=["department_code"])
        manager = self.create_user("scope-review-manager", "general_manager")
        conversation = AgentConversation.objects.create(owner=owner, department_code="finance")
        old_work, old_requirement = self._work(owner, conversation, "old private goal marker",
            "old private message marker", state="completed")
        root = AgentRun.objects.create(id=conversation.root_run_id, root_run_id=conversation.root_run_id,
            conversation=conversation, work=old_work, requirement=old_requirement, state="running",
            policy={"backend_private_marker": "backend marker"})

        record = {"project_id": "SCOPE-REVIEW", "project_name": "Published test record"}
        workbook = BusinessLedgerWorkbook.objects.create(department="finance", as_of=date.today(),
            created_by=owner, updated_by=owner)
        published = BusinessLedgerRevision.objects.create(workbook=workbook, revision=1,
            state=BusinessLedgerWorkbook.State.PUBLISHED, source_name="Synthetic published",
            as_of=date.today(), records=[record], record_meta={}, checksum="", action="publish", actor=owner)
        published.checksum = _checksum(workbook, records=published.records, state=published.state)
        published.save(update_fields=["checksum"])
        reference = AgentBusinessReference.objects.create(root=root, work=old_work,
            requirement=old_requirement, domain_type="business_revision", object_id=str(published.pk),
            revision=str(published.revision), digest=published.checksum, operation_key="history-public",
            public_summary="safe historical reference")

        current_work, current_requirement = self._work(owner, conversation, "new private goal marker",
            "new private message marker", version=1)
        AgentBusinessReference.objects.create(root=root, work=current_work,
            requirement=current_requirement, domain_type="finance_record", object_id="private-record",
            revision="2", operation_key="history-private", public_summary="private reference marker")
        AgentRun.objects.filter(pk=root.pk).update(work=current_work, requirement=current_requirement)

        factory = APIRequestFactory()
        request = factory.get("/api/agent/management/work/")
        force_authenticate(request, user=manager)
        listing = agent_api.management_work(request)
        self.assertEqual(listing.status_code, 200)
        items = {item["id"]: item for item in listing.data["items"]}
        old_item = items[str(old_work.pk)]
        current_item = items[str(current_work.pk)]
        self.assertEqual([item["reference_id"] for item in old_item["business_references"]],
                         [str(reference.pk)])
        self.assertEqual(current_item["business_references"], [])
        self.assertNotIn("goal", old_item)

        request = factory.get(f"/api/agent/management/references/{reference.pk}/")
        request.agent_user = manager
        force_authenticate(request, user=manager)
        detail = agent_management.reference_detail(request, str(reference.pk))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["data"]["checksum"], published.checksum)
        safe_output = repr(listing.data) + repr(detail.data)
        for marker in ("old private goal marker", "old private message marker", "new private goal marker",
                       "new private message marker", "backend marker", "private reference marker"):
            self.assertNotIn(marker, safe_output)

        mismatched = AgentBusinessReference.objects.create(root=root, work=old_work,
            requirement=old_requirement, domain_type="business_revision", object_id=str(published.pk),
            revision=str(published.revision), digest="0" * 64, operation_key="history-bad-digest",
            public_summary="mismatched reference")
        request = factory.get(f"/api/agent/management/references/{mismatched.pk}/")
        request.agent_user = manager
        force_authenticate(request, user=manager)
        detail = agent_management.reference_detail(request, str(mismatched.pk))
        self.assertEqual(detail.status_code, 409, detail.data)

        request = factory.get(f"/api/agent/management/references/{reference.pk}/")
        request.agent_user = owner
        force_authenticate(request, user=owner)
        self.assertEqual(agent_management.reference_detail(request, str(reference.pk)).status_code, 403)
        request = factory.post(f"/api/agent/management/references/{reference.pk}/", {}, format="json")
        request.agent_user = manager
        force_authenticate(request, user=manager)
        self.assertEqual(agent_management.reference_detail(request, str(reference.pk)).status_code, 405)

    def test_newer_work_source_registration_does_not_mask_revoked_old_source(self):
        owner = self.create_user("scope-review-hr", "hr")
        owner.department_code = "hr"
        owner.save(update_fields=["department_code"])
        conversation = AgentConversation.objects.create(owner=owner, department_code="hr")
        old_work, old_requirement = self._work(owner, conversation, "old HR work", "old request", version=1)
        root = AgentRun.objects.create(id=conversation.root_run_id, root_run_id=conversation.root_run_id,
            conversation=conversation, work=old_work, requirement=old_requirement, state="running",
            deadline_at=timezone.now() + timedelta(minutes=5),
            policy={"max_actions": 30, "max_model_calls": 10, "max_tool_calls": 12,
                    "max_launches": 6, "max_concurrent": 6, "max_active_ms": 60000})
        tools = AgentTools(RuntimeGuard(initialize_root(root.pk, owner.pk)))
        request = RecruitmentRequest.objects.create(created_by=owner, updated_by=owner,
            position_name="Synthetic role", original_text="Synthetic request")
        old_jd = JDVersion.objects.create(request=request, version=1, input_version=1,
            body="Old authorized description", requirements={}, created_by=owner)
        tools.hr_read_jd(str(request.pk), "first-version-read")

        current_work, current_requirement = self._work(owner, conversation, "new HR work", "new request", version=1)
        AgentRun.objects.filter(pk=root.pk).update(work=current_work, requirement=current_requirement)
        request.input_version = 2
        request.position_name = "Updated synthetic role"
        request.save(update_fields=["input_version", "position_name", "updated_at"])
        new_jd = JDVersion.objects.create(request=request, version=2, input_version=2,
            body="New authorized description", requirements={}, created_by=owner)
        tools.hr_read_jd(str(request.pk), "second-version-read")

        sources = read_sources(root)
        self.assertEqual({source["jd_id"] for source in sources if source["kind"] == "hr_jd"},
                         {str(old_jd.pk), str(new_jd.pk)})
        check_read_sources(root)
        old_jd.delete()
        with self.assertRaisesRegex(AgentDenied, "source_authorization_changed"):
            check_read_sources(root)
