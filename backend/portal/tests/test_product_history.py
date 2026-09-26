import hashlib
import uuid
from unittest.mock import patch
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings

from .base import PortalTestCase, json_body


class ProductHistoryTests(PortalTestCase):
    def setUp(self):
        self.owner = self.create_user("history-owner", "product")
        self.reviewer = self.create_user("history-reviewer", "product")
        self.other = self.create_user("history-other", "product")
        self.override = override_settings(
            PRODUCT_P1_ENABLED=True,
            PRODUCT_MODEL_CALLS_ALLOWED=False,
            PRODUCT_FORMAL_RELEASE_ENABLED=False,
            PRODUCT_REVIEWER_IDS=(self.reviewer.pk,),
            PRODUCT_STORAGE_ROOT=Path.cwd() / ".runtime" / "history-test-unused",
            PRODUCT_UPLOAD_MAX_BYTES=1024 * 1024,
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.owner_client = Client()
        self.reviewer_client = Client()
        self.other_client = Client()
        self.login(self.owner_client, self.owner)
        self.login(self.reviewer_client, self.reviewer)
        self.login(self.other_client, self.other)

    @staticmethod
    def input_payload(quantity=2):
        return {
            "project": "版本留痕隔离项目",
            "requirements": "形成可追溯技术方案",
            "items": [{"row_id": "1", "name": "防火墙", "quantity": quantity, "unit": "台"}],
            "background": "仅使用本任务资料。",
            "conditions": ["不得新增清单外设备"],
        }

    def create_task(self):
        response = self.owner_client.post(
            "/api/product/tasks/",
            json_body(title="版本留痕技术方案", input=self.input_payload(), reviewer_id=self.reviewer.pk),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="history-task",
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_authorized_history_tracks_diff_hash_roles_and_external_author_boundary(self):
        task = self.create_task()
        changed = self.owner_client.patch(
            f"/api/product/tasks/{task['id']}/",
            json_body(expected_version=task["version"], input=self.input_payload(quantity=3)),
            content_type="application/json",
        )
        self.assertEqual(changed.status_code, 200, changed.content)
        task = changed.json()
        uploaded_bytes = "外部上传资料，只证明收到该文件。".encode()
        with patch("portal.product_service.write_source", return_value=(
            uuid.uuid4(), "isolated/source.txt", hashlib.sha256(uploaded_bytes).hexdigest(),
        )):
            uploaded = self.owner_client.post(
                f"/api/product/tasks/{task['id']}/sources/",
                {"expected_version": task["version"], "file": SimpleUploadedFile("外部资料.txt", uploaded_bytes)},
            )
        self.assertEqual(uploaded.status_code, 201, uploaded.content)
        task = uploaded.json()["task"]
        blueprint = {
            "purpose": "形成技术方案",
            "audience": "项目评审人员",
            "chapters": [{"id": "overview", "title": "项目概述", "scope": "说明范围", "source_ids": ["1"]}],
            "conditions": [{"text": "不得新增清单外设备", "type": "program"}],
            "missing": [],
            "conflicts": [],
            "template_version": "frozen-original-v1",
        }
        saved = self.owner_client.patch(
            f"/api/product/tasks/{task['id']}/blueprint/",
            json_body(expected_version=task["version"], payload=blueprint),
            content_type="application/json",
        )
        self.assertEqual(saved.status_code, 200, saved.content)
        task = saved.json()
        denied = self.reviewer_client.post(
            f"/api/product/tasks/{task['id']}/decisions/",
            json_body(expected_version=task["version"], target="blueprint", target_id=task["blueprint"]["id"],
                      sha256=task["blueprint"]["sha256"], decision="approve", comment="旧审核人不得代替所有者确认"),
            content_type="application/json",
        )
        self.assertEqual(denied.status_code, 404, denied.content)
        approved = self.owner_client.post(
            f"/api/product/tasks/{task['id']}/decisions/",
            json_body(expected_version=task["version"], target="blueprint", target_id=task["blueprint"]["id"],
                      sha256=task["blueprint"]["sha256"], decision="approve", comment="同意当前精确蓝图"),
            content_type="application/json",
        )
        self.assertEqual(approved.status_code, 201, approved.content)

        url = f"/api/product/tasks/{task['id']}/history/"
        response = self.owner_client.get(url)
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertTrue(payload["history_immutable"])
        self.assertEqual(payload["tamper_claim"], "application_read_only_not_forensic_immutability")
        revisions = [item for item in payload["timeline"] if item["event"] == "revision" and item["kind"] == "input"]
        self.assertEqual([item["version"] for item in revisions], [1, 2, 3])
        self.assertEqual(revisions[0]["reason"], "task_created")
        self.assertEqual(revisions[0]["actor"]["type"], "initiator")
        self.assertEqual(revisions[1]["reason"], "manual_input_edit")
        self.assertEqual(revisions[1]["parent_sha256"], revisions[0]["sha256"])
        quantity_diff = next(item for item in revisions[1]["diff"] if item["path"] == "$.items[0].quantity")
        self.assertEqual((quantity_diff["before"], quantity_diff["after"]), (2, 3))
        self.assertEqual(revisions[2]["reason"], "external_source_upload")
        upload = next(item for item in payload["timeline"] if item["event"] == "external_upload")
        self.assertEqual(upload["sha256"], hashlib.sha256(uploaded_bytes).hexdigest())
        self.assertEqual(upload["uploader"]["id"], self.owner.pk)
        self.assertEqual(upload["content_author"], {"status": "unverified", "id": None, "name": None})
        approval = next(item for item in payload["timeline"] if item["event"] == "approval")
        self.assertEqual(approval["actor"]["type"], "approver")
        self.assertEqual(approval["actor"]["id"], self.owner.pk)
        self.assertEqual(approval["reason"], "同意当前精确蓝图")
        self.assertTrue(approval["authorization_current"])

        self.assertEqual(self.reviewer_client.get(url).status_code, 200)
        self.assertEqual(self.other_client.get(url).status_code, 404)
        self.assertEqual(self.owner_client.delete(url).status_code, 405)
        self.assertEqual(self.owner_client.patch(url, data="{}", content_type="application/json").status_code, 405)