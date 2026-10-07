from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from portal import agent_api, business_boards
from portal.agent_finance import reconcile_finance_work
from portal.agent_models import (AgentBusinessReference, AgentConversation, AgentEvent, AgentMessage,
                                 AgentRequirement, AgentRun, AgentWorkTask)
from portal.agent_runtime import AgentDenied, RuntimeGuard, initialize_root
from portal.agent_tools import AgentTools
from portal.business_models import BusinessLedgerGrant, BusinessLedgerRevision, BusinessLedgerWorkbook

from .base import PortalTestCase


@override_settings(AGENT_PLATFORM_ENABLED=True)
class FinanceCompletionTests(TransactionTestCase):
    create_user = PortalTestCase.create_user

    def setUp(self):
        call_command("seed_portal", stdout=StringIO())
        self.owner = self.create_user("finance-completion-owner")
        self.owner.department_code = "finance"
        self.owner.save(update_fields=["department_code"])
        self.grant = BusinessLedgerGrant.objects.create(user=self.owner, department="finance", can_edit=True)
        self.owner.refresh_from_db()
        conversation = AgentConversation.objects.create(owner=self.owner, department_code="finance")
        self.work = AgentWorkTask.objects.create(owner=self.owner, conversation=conversation,
            department_code="finance", goal="核对本人财务记录", state="running", current_requirement_version=1)
        message = AgentMessage.objects.create(conversation=conversation, work=self.work,
                                             role="user", content=self.work.goal)
        requirement = AgentRequirement.objects.create(work=self.work, version=1,
            user_message=message, content=message.content)
        self.root = AgentRun.objects.create(id=conversation.root_run_id, root_run_id=conversation.root_run_id,
            conversation=conversation, work=self.work, requirement=requirement, state="running",
            deadline_at=timezone.now() + timedelta(hours=1), policy={"max_actions":40,
                "max_model_calls":20, "max_tool_calls":20, "max_launches":10,
                "max_concurrent":5, "max_active_ms":3600000})
        self.guard = RuntimeGuard(initialize_root(self.root.pk, self.owner.pk))
        self.tools = AgentTools(self.guard)
        self.factory = APIRequestFactory()

    def draft(self, project_id="A"):
        workbook = BusinessLedgerWorkbook.objects.filter(department="finance").first()
        return self.tools.finance_save_draft({"project_id":project_id, "project_name":project_id,
            "contract_amount":"100.00", "received_amount":"10.00", "due_date":""},
            workbook.revision if workbook else 0, f"draft-{project_id}")

    def publish(self, projects=("A",), owner=None):
        workbook = BusinessLedgerWorkbook.objects.get(department="finance")
        baseline = BusinessLedgerRevision.objects.filter(workbook=workbook, state="published").first()
        selected = [{"record_id":record_id, "source_revision":entry["draft_revision"],
                     "source_checksum":entry["draft_checksum"]}
                    for record_id, entry in workbook.record_meta.items() if entry["project_id"] in projects]
        request = self.factory.post("/api/business/ledgers/finance/publish/", {
            "expected_revision":workbook.revision, "expected_published_revision":baseline.revision if baseline else 0,
            "records":selected}, format="json")
        force_authenticate(request, user=owner or self.owner)
        return business_boards.ledger_publish(request, "finance")

    def test_draft_is_not_delivery_and_exact_self_publish_closes_work(self):
        self.draft()
        self.assertFalse(reconcile_finance_work(self.work.pk))
        self.work.refresh_from_db()
        self.assertEqual(self.work.state, "waiting_confirmation")
        self.assertEqual(self.publish().status_code, 200)
        self.root.refresh_from_db()
        before = (self.root.action_count, self.root.launch_count, self.root.deadline_at, self.root.policy)
        request = self.factory.get("/api/agent/work/")
        force_authenticate(request, user=self.owner)
        response = agent_api.work_detail(request, self.work.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["state"], "completed")
        result = response.data["result_references"][0]
        revision = BusinessLedgerRevision.objects.get(pk=result["object_id"])
        self.assertEqual((result["domain_type"], result["revision"], result["digest"]),
                         ("business_revision", str(revision.revision), revision.checksum))
        self.assertFalse(self.grant.can_publish)
        self.root.refresh_from_db()
        self.assertEqual(before, (self.root.action_count, self.root.launch_count,
                                  self.root.deadline_at, self.root.policy))
        self.assertEqual(self.guard.check().pk, self.root.pk)
        with self.assertRaises(AgentDenied):
            self.guard.check(write=True)

    def test_all_latest_own_drafts_must_be_in_the_confirmed_snapshot(self):
        self.draft("A")
        self.draft("B")
        self.assertEqual(self.publish(("A",)).status_code, 200)
        self.assertFalse(reconcile_finance_work(self.work.pk))
        self.assertEqual(self.publish(("B",)).status_code, 200)
        self.assertTrue(reconcile_finance_work(self.work.pk))

    def test_other_employee_cannot_complete_by_publishing_owner_draft(self):
        self.draft()
        other = self.create_user("finance-completion-other")
        BusinessLedgerGrant.objects.create(user=other, department="finance", can_edit=True, can_publish=True)
        self.assertEqual(self.publish(owner=other).status_code, 403)
        self.assertFalse(reconcile_finance_work(self.work.pk))

    def test_completed_poll_is_idempotent(self):
        self.draft()
        self.assertEqual(self.publish().status_code, 200)
        self.assertTrue(reconcile_finance_work(self.work.pk))
        self.assertFalse(reconcile_finance_work(self.work.pk))
        self.assertEqual(AgentBusinessReference.objects.filter(domain_type="business_revision").count(), 1)
        self.assertEqual(AgentEvent.objects.filter(type="work_completed").count(), 1)

    def test_cancelled_work_is_not_completed_by_later_legacy_self_publish(self):
        self.draft()
        AgentWorkTask.objects.filter(pk=self.work.pk).update(state="cancelled")
        self.assertEqual(self.publish().status_code, 200)
        self.assertFalse(reconcile_finance_work(self.work.pk))
        self.work.refresh_from_db()
        self.assertEqual(self.work.state, "cancelled")

    def test_old_requirement_publication_does_not_complete_new_requirement(self):
        self.draft()
        message = AgentMessage.objects.create(conversation=self.work.conversation, work=self.work,
                                             role="user", content="更正要求")
        requirement = AgentRequirement.objects.create(work=self.work, version=2,
            user_message=message, content=message.content)
        AgentWorkTask.objects.filter(pk=self.work.pk).update(current_requirement_version=2)
        AgentRun.objects.filter(pk=self.root.pk).update(requirement=requirement)
        self.assertEqual(self.publish().status_code, 200)
        self.assertFalse(reconcile_finance_work(self.work.pk))

    def test_revoke_blocks_completion_even_when_old_publication_exists(self):
        self.draft()
        self.assertEqual(self.publish().status_code, 200)
        self.grant.delete()
        self.assertFalse(reconcile_finance_work(self.work.pk))
        self.work.refresh_from_db()
        self.assertNotEqual(self.work.state, "completed")
