from datetime import timedelta
from unittest.mock import patch

from django.test import Client, override_settings
from django.urls import include, path
from django.utils import timezone

from portal.hr_models import HrJobTask, ProbationCase
from portal.models import Module, Role
from portal.product_models import DocumentApproval, DocumentArtifact, DocumentRevision, DocumentTask
from portal.work_summary import summary

from .base import PortalTestCase


urlpatterns = [path("api/work/summary/", summary), path("", include("config.urls"))]


@override_settings(ROOT_URLCONF=__name__, PRODUCT_REVIEWER_IDS=())
class WorkSummaryTests(PortalTestCase):
    def setUp(self):
        self.user = self.create_user("summary-user", "product", "hr")
        self.other = self.create_user("summary-other", "product", "hr")
        self.client = Client()
        self.login(self.client, self.user)

    def product_task(self, owner=None, **values):
        owner = owner or self.user
        defaults = {
            "owner": owner,
            "title": "技术方案",
            "idempotency_key": f"summary-{DocumentTask.objects.count()}",
            "payload_hash": "a" * 64,
        }
        defaults.update(values)
        return DocumentTask.objects.create(**defaults)

    def test_counts_links_and_recent_limit_are_object_scoped(self):
        product = self.product_task()
        self.product_task(owner=self.other, title="他人产品任务")
        job = HrJobTask.objects.create(owner=self.user, title="实施工程师", state=HrJobTask.State.CONFIRMED)
        HrJobTask.objects.create(owner=self.other, title="他人岗位")
        manager_case = ProbationCase.objects.create(
            owner=self.other, assigned_manager=self.user, employee_name="员工甲", position="实施工程师",
            state=ProbationCase.State.MANAGER_PENDING,
        )
        ProbationCase.objects.create(
            owner=self.other, assigned_manager=self.other, employee_name="不可见员工", position="工程师",
            state=ProbationCase.State.MANAGER_PENDING,
        )
        archived = ProbationCase.objects.create(
            owner=self.other, assigned_manager=self.user, employee_name="已归档员工", position="工程师",
            state=ProbationCase.State.ARCHIVED,
        )
        product.reviewer = self.other
        product.save(update_fields=["reviewer", "updated_at"])
        artifact = DocumentArtifact.objects.create(
            task=product, version=1, path="unused.docx", sha256="b" * 64,
            blueprint_hash="c" * 64, input_hash="d" * 64, template_hash="e" * 64,
        )
        approval = DocumentApproval.objects.create(
            task=product, artifact=artifact, actor=self.other,
            decision=DocumentApproval.Decision.APPROVE, sha256=artifact.sha256,
        )

        with patch("portal.work_summary.effective_artifact_approval", return_value=approval):
            response = self.client.get("/api/work/summary/")

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["sections"]["my_tasks"]["count"], 2)
        self.assertEqual(
            {item["href"] for item in body["sections"]["my_tasks"]["items"]},
            {f"/centers/product/documents?task={product.pk}", f"/centers/hr/job?task={job.pk}"},
        )
        self.assertEqual(body["sections"]["pending_reviews"]["count"], 1)
        self.assertEqual(body["sections"]["pending_reviews"]["items"][0]["id"], str(manager_case.pk))
        self.assertEqual(body["sections"]["pending_reviews"]["items"][0]["kind"], "hr_probation")
        results = body["sections"]["recent_results"]
        self.assertEqual(results["count"], 3)
        self.assertEqual(
            {item["href"] for item in results["items"]},
            {
                f"/centers/product/documents?task={product.pk}&artifact={artifact.pk}",
                f"/centers/hr/job?task={job.pk}",
                f"/centers/hr/probation?case={archived.pk}",
            },
        )
        for section in body["sections"].values():
            for item in section["items"]:
                self.assertEqual(set(item), {"id", "title", "status", "href", "updated_at", "kind"})

        for index in range(5):
            task = self.product_task(title=f"最近任务 {index}")
            DocumentTask.objects.filter(pk=task.pk).update(updated_at=timezone.now() + timedelta(minutes=index))
        limited = self.client.get("/api/work/summary/").json()["sections"]["my_tasks"]
        self.assertEqual(limited["count"], 7)
        self.assertEqual(len(limited["items"]), 3)
        self.assertEqual([item["title"] for item in limited["items"]], ["最近任务 4", "最近任务 3", "最近任务 2"])

    def test_module_revocation_removes_that_modules_objects_without_leaking_counts(self):
        self.product_task()
        HrJobTask.objects.create(owner=self.user, title="人事任务")
        self.user.roles.remove(Role.objects.get(code="product"))

        body = self.client.get("/api/work/summary/").json()

        self.assertEqual(body["modules"]["product"], {"available": False, "reason": "未获授权访问产品模块。"})
        self.assertEqual(body["modules"]["hr"], {"available": True})
        self.assertEqual(body["sections"]["my_tasks"]["count"], 1)
        self.assertEqual(body["sections"]["my_tasks"]["items"][0]["kind"], "hr_job")
        self.assertIn("仅汇总已授权模块", body["sections"]["my_tasks"]["reason"])

    def test_long_term_hr_archive_remains_visible_but_legacy_expired_stays_hidden(self):
        active = HrJobTask.objects.create(owner=self.user, title="长期岗位", archive_state="active")
        expired = HrJobTask.objects.create(owner=self.user, title="旧失效岗位", archive_state="legacy_expired")
        HrJobTask.objects.filter(pk__in=[active.pk, expired.pk]).update(
            created_at=timezone.now() - timedelta(days=90))
        body = self.client.get("/api/work/summary/").json()
        titles = [item["title"] for item in body["sections"]["my_tasks"]["items"]]
        self.assertIn("长期岗位", titles)
        self.assertNotIn("旧失效岗位", titles)

    def test_revoked_product_source_removes_task_titles_and_counts_for_owner_and_reviewer(self):
        for owner, reviewer in ((self.user, self.other), (self.other, self.user)):
            task = self.product_task(owner=owner, reviewer=reviewer, title="REVOKED_PRIVATE_TITLE",
                state=DocumentTask.State.WAITING_REVIEW, input_version=1)
            DocumentRevision.objects.create(task=task, kind="input", version=1, sha256="f" * 64,
                payload={"authorization_dependencies": [{"invalid": "revoked"}]})
        with override_settings(PRODUCT_REVIEWER_IDS=(self.user.pk,)):
            response = self.client.get("/api/work/summary/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"REVOKED_PRIVATE_TITLE", response.content)
        for section in response.json()["sections"].values():
            self.assertEqual(section["count"], 0)

    def test_no_business_module_reports_unknown_instead_of_zero(self):
        outsider = self.create_user("summary-outsider")
        client = Client()
        self.login(client, outsider)

        body = client.get("/api/work/summary/").json()

        self.assertFalse(body["sections"]["my_tasks"]["available"])
        self.assertIsNone(body["sections"]["my_tasks"]["count"])
        self.assertEqual(body["sections"]["my_tasks"]["items"], [])
        self.assertIn("未获授权", body["sections"]["my_tasks"]["reason"])

    def test_platform_admin_without_business_role_cannot_read_business_data(self):
        self.product_task()
        admin = self.create_admin("summary-admin")
        Role.objects.get(code="platform_admin").modules.add(Module.objects.get(code="product"))
        client = Client()
        self.login(client, admin, password="Admin!Pass9274-Qx")

        body = client.get("/api/work/summary/").json()

        self.assertFalse(body["modules"]["product"]["available"])
        self.assertIsNone(body["sections"]["my_tasks"]["count"])

    @override_settings(PRODUCT_REVIEWER_IDS=())
    def test_pending_product_review_requires_current_reviewer_authorization(self):
        task = self.product_task(
            owner=self.other, reviewer=self.user, state=DocumentTask.State.WAITING_REVIEW,
            stage=DocumentTask.Stage.BLUEPRINT,
        )
        self.assertEqual(self.client.get("/api/work/summary/").json()["sections"]["pending_reviews"]["count"], 0)

        with override_settings(PRODUCT_REVIEWER_IDS=(self.user.pk,)):
            section = self.client.get("/api/work/summary/").json()["sections"]["pending_reviews"]
        self.assertEqual(section["count"], 1)
        self.assertEqual(section["items"][0]["id"], str(task.pk))

    def test_assigned_manager_without_hr_role_sees_only_own_pending_approval(self):
        manager = self.create_user("summary-manager")
        unrelated_manager = self.create_user("summary-unrelated-manager")
        own = ProbationCase.objects.create(
            owner=self.other, assigned_manager=manager, employee_name="员工甲", position="工程师",
            state=ProbationCase.State.MANAGER_PENDING,
        )
        ProbationCase.objects.create(
            owner=self.other, assigned_manager=unrelated_manager, employee_name="员工乙", position="工程师",
            state=ProbationCase.State.MANAGER_PENDING,
        )
        client = Client()
        self.login(client, manager)

        body = client.get("/api/work/summary/").json()

        self.assertEqual(body["modules"]["hr"], {"available": True})
        self.assertEqual(body["sections"]["pending_reviews"]["count"], 1)
        self.assertEqual(body["sections"]["pending_reviews"]["items"][0]["id"], str(own.pk))
        self.assertEqual(body["sections"]["my_tasks"]["count"], 0)
        self.assertEqual(body["sections"]["recent_results"]["count"], 0)

    def test_manager_fallback_does_not_restore_revoked_owner_data_and_requires_enabled_module(self):
        job = HrJobTask.objects.create(owner=self.user, title="已撤权岗位")
        owned = ProbationCase.objects.create(
            owner=self.user, assigned_manager=self.other, employee_name="自有员工", position="工程师",
        )
        assigned = ProbationCase.objects.create(
            owner=self.other, assigned_manager=self.user, employee_name="待审员工", position="工程师",
            state=ProbationCase.State.MANAGER_PENDING,
        )
        self.user.roles.remove(Role.objects.get(code="hr"))

        body = self.client.get("/api/work/summary/").json()
        self.assertEqual(body["modules"]["hr"], {"available": True})
        visible_ids = {
            item["id"] for section in body["sections"].values() for item in section["items"]
        }
        self.assertIn(str(assigned.pk), visible_ids)
        self.assertNotIn(str(job.pk), visible_ids)
        self.assertNotIn(str(owned.pk), visible_ids)

        self.user.roles.remove(Role.objects.get(code="product"))
        module = Module.objects.get(code="hr")
        module.enabled = False
        module.save(update_fields=["enabled"])
        disabled = self.client.get("/api/work/summary/").json()
        self.assertFalse(disabled["modules"]["hr"]["available"])
        self.assertFalse(disabled["sections"]["pending_reviews"]["available"])
        self.assertIsNone(disabled["sections"]["pending_reviews"]["count"])
