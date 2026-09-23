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
        self.reviewer.roles.remove(role)
        self.reviewer.roles.add(role)
        self.assertIsNone(approved_blueprint(record))

    def test_observed_reviewer_policy_revocation_cannot_restore_old_approval(self):
        task = self.save_blueprint(self.create_task())
        self.approve_blueprint(task)
        record = DocumentTask.objects.get(pk=task["id"])
        with override_settings(PRODUCT_REVIEWER_IDS=()):
            self.assertIsNone(approved_blueprint(record))
        self.assertIsNone(approved_blueprint(record))

    def post(self, client, task, endpoint, **body):
        return client.post(f"/api/product/tasks/{task['id']}/{endpoint}/",
                           json_body(expected_version=task["version"], **body), content_type="application/json")

    def test_assign_after_creation_and_reassignment_never_revives_old_approval(self):
        task = self.create_task(reviewer=False)
        rejected = self.post(self.owner_client, task, "reviewer", reviewer_id=self.owner.pk, reason="自审")
        self.assertEqual(rejected.status_code, 400)
        assigned = self.post(self.owner_client, task, "reviewer", reviewer_id=self.reviewer.pk, reason="隔离测试明确指定")
        self.assertEqual(assigned.status_code, 200, assigned.content)
        task = self.save_blueprint(assigned.json())
        self.approve_blueprint(task)
        task = self.owner_client.get(f"/api/product/tasks/{task['id']}/").json()
        original_blueprint = task["blueprint"]["id"]
        with override_settings(PRODUCT_REVIEWER_IDS=(self.reviewer.pk, self.other.pk)):
            changed = self.post(self.owner_client, task, "reviewer", reviewer_id=self.other.pk, reason="测试改派")
            self.assertEqual(changed.status_code, 200, changed.content)
            back = self.post(self.owner_client, changed.json(), "reviewer", reviewer_id=self.reviewer.pk, reason="测试重新指定")
            self.assertEqual(back.status_code, 200, back.content)
        self.assertNotEqual(back.json()["blueprint"]["id"], original_blueprint)
        self.assertIsNone(approved_blueprint(DocumentTask.objects.get(pk=task["id"])))
        self.assertEqual(self.post(self.owner_client, task, "reviewer", reviewer_id=self.reviewer.pk, reason="陈旧请求").status_code, 409)

    def test_statement_resolution_is_reviewer_only_and_bound_to_input(self):
        task = self.create_task()
        result = self.post(self.owner_client, task, "statements", category="conflict", text="背景与清单数量需核对", source_ids=["1"])
        self.assertEqual(result.status_code, 200, result.content)
        task = result.json()
        resolution = {"issue_hash": task["input_issues"][0]["issue_hash"], "category": "fact", "reason": "核对合成清单，采用清单数量", "source_ids": ["1"]}
        self.assertEqual(self.post(self.owner_client, task, "input-review", resolutions=[resolution]).status_code, 404)
        reviewed = self.post(self.reviewer_client, task, "input-review", resolutions=[resolution])
        self.assertEqual(reviewed.status_code, 200, reviewed.content)
        task = reviewed.json()
        self.assertEqual(task["input_issues"], [])
        self.assertEqual(task["input"]["issue_resolutions"][0]["actor_id"], self.reviewer.pk)
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

    def test_reassignment_reopens_previous_reviewers_input_resolution(self):
        task = self.post(self.owner_client, self.create_task(), "statements", category="inference", text="待核推断", source_ids=["1"]).json()
        item = {"issue_hash": task["input_issues"][0]["issue_hash"], "category": "inference", "reason": "隔离人工核对", "source_ids": ["1"]}
        resolved = self.post(self.reviewer_client, task, "input-review", resolutions=[item])
        self.assertEqual(resolved.status_code, 200, resolved.content)
        with override_settings(PRODUCT_REVIEWER_IDS=(self.reviewer.pk, self.other.pk)):
            changed = self.post(self.owner_client, resolved.json(), "reviewer", reviewer_id=self.other.pk, reason="隔离改派复验")
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual(len(changed.json()["input_issues"]), 1)
        self.assertEqual(changed.json()["input"]["issue_resolutions"], [])
        self.assertEqual(len(changed.json()["input"]["issue_history"]), 1)

    def test_running_task_rejects_reassignment_and_input_review(self):
        task = self.create_task()
        DocumentTask.objects.filter(pk=task["id"]).update(state="RUNNING")
        self.assertEqual(self.post(self.owner_client, task, "reviewer", reviewer_id=self.reviewer.pk, reason="执行中改派").status_code, 409)
        self.assertEqual(self.post(self.reviewer_client, task, "input-review", resolutions=[]).status_code, 409)
