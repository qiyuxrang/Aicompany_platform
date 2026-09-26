from django.test import override_settings

from portal.product_models import DocumentTask
from portal.product_service import approved_blueprint
from portal.models import Role

from .base import PortalTestCase, json_body
from .test_product_api import ProductApiTests


class ProductIncrementTests(PortalTestCase):
    setUp = ProductApiTests.setUp
    input_payload = ProductApiTests.input_payload
    create_task = ProductApiTests.create_task
    blueprint_payload = ProductApiTests.blueprint_payload
    save_blueprint = ProductApiTests.save_blueprint
    approve_blueprint = ProductApiTests.approve_blueprint

    def test_revoke_then_restore_role_does_not_revive_old_approval(self):
        task = self.save_blueprint(self.create_task())
        self.approve_blueprint(task)
        record = DocumentTask.objects.get(pk=task["id"])
        self.assertIsNotNone(approved_blueprint(record))
        role = Role.objects.get(code="product")
        self.owner.roles.remove(role)
        self.owner.roles.add(role)
        self.assertIsNone(approved_blueprint(record))

    def test_reviewer_policy_changes_do_not_gate_owner_confirmation(self):
        task = self.save_blueprint(self.create_task())
        self.approve_blueprint(task)
        record = DocumentTask.objects.get(pk=task["id"])
        with override_settings(PRODUCT_REVIEWER_IDS=()):
            self.assertIsNotNone(approved_blueprint(record))
        self.assertIsNotNone(approved_blueprint(record))

    def post(self, client, task, endpoint, **body):
        return client.post(f"/api/product/tasks/{task['id']}/{endpoint}/",
                           json_body(expected_version=task["version"], **body), content_type="application/json")

    def test_removed_reviewer_assignment_never_mutates_owner_workflow(self):
        task = self.create_task(reviewer=False)
        for reviewer_id in (self.owner.pk, self.reviewer.pk, self.other.pk):
            rejected = self.post(self.owner_client, task, "reviewer", reviewer_id=reviewer_id, reason="兼容旧请求")
            self.assertEqual(rejected.status_code, 404)
        unchanged = self.owner_client.get(f"/api/product/tasks/{task['id']}/").json()
        self.assertEqual(unchanged["version"], task["version"])
        self.assertIsNone(unchanged["reviewer_id"])
        saved = self.save_blueprint(unchanged)
        approved = self.approve_blueprint(saved)["task"]
        self.assertIsNotNone(approved_blueprint(DocumentTask.objects.get(pk=task["id"])))
        self.assertEqual(approved["pending_action"], "generate_outputs")
        self.assertEqual(self.post(self.owner_client, approved, "reviewer", reviewer_id=self.reviewer.pk, reason="不得改派").status_code, 404)

    def test_owner_statement_resolution_is_explicit_and_bound_to_input(self):
        task = self.create_task()
        result = self.post(self.owner_client, task, "statements", category="conflict", text="背景与清单数量需核对", source_ids=["1"])
        self.assertEqual(result.status_code, 200, result.content)
        task = result.json()
        resolution = {"issue_hash": task["input_issues"][0]["issue_hash"], "category": "fact", "reason": "核对合成清单，采用清单数量", "source_ids": ["1"]}
        self.assertEqual(self.post(self.other_client, task, "input-review", resolutions=[resolution]).status_code, 404)
        reviewed = self.post(self.owner_client, task, "input-review", resolutions=[resolution])
        self.assertEqual(reviewed.status_code, 200, reviewed.content)
        task = reviewed.json()
        self.assertEqual(task["input_issues"], [])
        self.assertEqual(task["input"]["issue_resolutions"][0]["actor_id"], self.owner.pk)
        changed_input = self.input_payload()
        changed_input["items"][0]["quantity"] = 3
        changed = self.owner_client.patch(f"/api/product/tasks/{task['id']}/", json_body(expected_version=task["version"], input=changed_input), content_type="application/json")
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual(len(changed.json()["input_issues"]), 1)
        self.assertEqual(len(changed.json()["input"]["issue_history"]), 1)
        self.assertEqual(changed.json()["input"]["issue_resolutions"], [])

    def test_invalid_source_or_stale_issue_never_resolves(self):
        task = self.post(self.owner_client, self.create_task(), "statements", category="inference", text="待核推断", source_ids=["1"]).json()
        item = {"issue_hash": task["input_issues"][0]["issue_hash"], "category": "inference", "reason": "人工核对", "source_ids": ["other-task"]}
        self.assertEqual(self.post(self.reviewer_client, task, "input-review", resolutions=[item]).status_code, 400)
        item.update(source_ids=["1"], issue_hash="0" * 64)
        self.assertEqual(self.post(self.reviewer_client, task, "input-review", resolutions=[item]).status_code, 409)

    def test_removed_reassignment_preserves_legacy_input_resolution_history(self):
        task = self.post(self.owner_client, self.create_task(), "statements", category="inference", text="待核推断", source_ids=["1"]).json()
        item = {"issue_hash": task["input_issues"][0]["issue_hash"], "category": "inference", "reason": "隔离人工核对", "source_ids": ["1"]}
        resolved = self.post(self.reviewer_client, task, "input-review", resolutions=[item])
        self.assertEqual(resolved.status_code, 200, resolved.content)
        with override_settings(PRODUCT_REVIEWER_IDS=(self.reviewer.pk, self.other.pk)):
            changed = self.post(self.owner_client, resolved.json(), "reviewer", reviewer_id=self.other.pk, reason="隔离改派复验")
        self.assertEqual(changed.status_code, 404, changed.content)
        current = self.owner_client.get(f"/api/product/tasks/{task['id']}/").json()
        self.assertEqual(current["version"], resolved.json()["version"])
        self.assertEqual(current["input_issues"], [])
        self.assertEqual(current["input"]["issue_resolutions"][0]["actor_id"], self.reviewer.pk)
        self.assertEqual(len(current["input"]["issue_history"]), 1)

    def test_running_task_rejects_reassignment_and_input_review(self):
        task = self.create_task()
        DocumentTask.objects.filter(pk=task["id"]).update(state="RUNNING")
        self.assertEqual(self.post(self.owner_client, task, "reviewer", reviewer_id=self.reviewer.pk, reason="执行中改派").status_code, 404)
        self.assertEqual(self.post(self.owner_client, task, "input-review", resolutions=[]).status_code, 409)
        self.assertEqual(self.post(self.reviewer_client, task, "input-review", resolutions=[]).status_code, 409)
