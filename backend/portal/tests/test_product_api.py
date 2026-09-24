import hashlib
import json
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.utils import timezone

from portal.models import Role
from portal.product_models import (DocumentApproval, DocumentArtifact,
                                   DocumentAttempt, DocumentRevision,
                                   DocumentSource, DocumentTask)
from portal.product_service import append_revision, approved_blueprint, digest, effective_artifact_approval, approval_authorization
from portal.product_storage import StorageError, parse_upload

from .base import PortalTestCase, json_body


class ProductApiTests(PortalTestCase):
    def setUp(self):
        self.storage = TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        self.owner = self.create_user("product-owner", "product")
        self.reviewer = self.create_user("product-reviewer", "product")
        self.other = self.create_user("product-other", "product")
        self.settings_override = override_settings(
            PRODUCT_P1_ENABLED=True,
            PRODUCT_MODEL_CALLS_ALLOWED=False,
            PRODUCT_FORMAL_RELEASE_ENABLED=False,
            PRODUCT_REVIEWER_IDS=(self.reviewer.pk,),
            PRODUCT_STORAGE_ROOT=Path(self.storage.name),
            PRODUCT_UPLOAD_MAX_BYTES=1024 * 1024,
            PRODUCT_MAX_ATTEMPTS=8,
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.owner_client = Client()
        self.reviewer_client = Client()
        self.other_client = Client()
        self.login(self.owner_client, self.owner)
        self.login(self.reviewer_client, self.reviewer)
        self.login(self.other_client, self.other)

    def input_payload(self, conditions=None):
        return {
            "project": "隔离试用项目",
            "requirements": "形成可追溯技术方案",
            "items": [{"row_id": "1", "name": "防火墙", "quantity": 2, "unit": "台"}],
            "background": "仅使用本任务资料。",
            "conditions": conditions or ["不得新增清单外设备"],
        }

    def test_product_writes_require_csrf_with_real_session(self):
        client = Client(enforce_csrf_checks=True)
        client.cookies = self.owner_client.cookies
        payload = json_body(title="CSRF隔离测试", input=self.input_payload())
        denied = client.post("/api/product/tasks/", payload, content_type="application/json", HTTP_IDEMPOTENCY_KEY="csrf-test")
        self.assertEqual(denied.status_code, 403)
        token = client.get("/api/csrf/").json()["csrfToken"]
        allowed = client.post("/api/product/tasks/", payload, content_type="application/json", HTTP_IDEMPOTENCY_KEY="csrf-test", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(allowed.status_code, 201, allowed.content)

    def test_disguised_and_oversize_uploads_leave_no_source(self):
        task = self.create_task()
        for name, content in (("payload.txt", b"MZ\x00binary"), ("payload.exe", b"text"), ("oversize.txt", b"x" * (1024 * 1024 + 1))):
            with self.subTest(name=name):
                response = self.owner_client.post(f"/api/product/tasks/{task['id']}/sources/", {
                    "expected_version": task["version"], "file": SimpleUploadedFile(name, content),
                })
                self.assertEqual(response.status_code, 400, response.content)
        detail = self.owner_client.get(f"/api/product/tasks/{task['id']}/").json()
        self.assertEqual(detail["sources"], [])

    def test_conversation_creates_task_and_imports_quoted_allowed_path_snapshot(self):
        import_root = Path(self.storage.name) / "approved-imports"
        import_root.mkdir()
        original = import_root / "项目背景.txt"
        original.write_text("只使用已批准的项目资料。", encoding="utf-8")
        message = f'请使用 {original} 编制这个项目的全部成果。'
        with override_settings(PRODUCT_IMPORT_ROOTS=(import_root,)):
            response = self.owner_client.post(
                "/api/product/conversations/",
                json_body(message=message),
                content_type="application/json",
                HTTP_IDEMPOTENCY_KEY="conversation-path",
            )
            self.assertEqual(response.status_code, 201, response.content)
            payload = response.json()
            self.assertEqual(payload["intent"]["mode"], "deterministic_fallback")
            self.assertFalse(payload["intent"]["model_called"])
            self.assertEqual(payload["intent"]["detected_paths"], [str(original)])
            self.assertEqual(
                payload["intent"]["requested_outputs"],
                [
                    {"type": "technical_solution", "status": "blocked", "code": "model_not_authorized"},
                    {"type": "feasibility", "status": "not_started", "code": "approved_content_required"},
                    {"type": "presentation", "status": "not_started", "code": "approved_content_required"},
                ],
            )
            source = DocumentSource.objects.get(task_id=payload["task"]["id"])
            snapshot = Path(self.storage.name) / source.path
            self.assertEqual(snapshot.read_text(encoding="utf-8"), "只使用已批准的项目资料。")
            self.assertEqual(source.sha256, hashlib.sha256(snapshot.read_bytes()).hexdigest())
            original.write_text("原文件后续已改动。", encoding="utf-8")
            self.assertEqual(snapshot.read_text(encoding="utf-8"), "只使用已批准的项目资料。")
            replay = self.owner_client.post(
                "/api/product/conversations/", json_body(message=message), content_type="application/json",
                HTTP_IDEMPOTENCY_KEY="conversation-path",
            )
            self.assertEqual(replay.status_code, 200, replay.content)
            self.assertEqual(DocumentSource.objects.filter(task_id=payload["task"]["id"]).count(), 1)

    def test_conversation_path_import_rejects_unsafe_and_unsupported_targets(self):
        import_root = Path(self.storage.name) / "approved-imports"
        import_root.mkdir()
        unsupported = import_root / "payload.pdf"
        unsupported.write_bytes(b"not-a-pdf")
        outside = Path(self.storage.name) / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        cases = (
            (str(import_root), "import_path_not_file"),
            (str(import_root / "missing.txt"), "import_path_missing"),
            (str(unsupported), "unsupported_file"),
            (str(import_root / ".." / outside.name), "import_path_not_allowed"),
        )
        with override_settings(PRODUCT_IMPORT_ROOTS=(import_root,)):
            for index, (target, code) in enumerate(cases):
                with self.subTest(code=code):
                    response = self.owner_client.post(
                        "/api/product/conversations/",
                        json_body(message="请生成技术方案", paths=[target]),
                        content_type="application/json",
                        HTTP_IDEMPOTENCY_KEY=f"unsafe-path-{index}",
                    )
                    self.assertEqual(response.status_code, 400, response.content)
                    self.assertEqual(response.json()["code"], code)
        self.assertFalse(DocumentTask.objects.filter(idempotency_key__startswith="unsafe-path-").exists())

    def test_conversation_path_import_requires_product_permission(self):
        import_root = Path(self.storage.name) / "approved-imports"
        import_root.mkdir()
        source = import_root / "private.txt"
        source.write_text("private", encoding="utf-8")
        user = self.create_user("conversation-no-product")
        client = Client()
        self.login(client, user)
        with override_settings(PRODUCT_IMPORT_ROOTS=(import_root,)):
            response = client.post(
                "/api/product/conversations/",
                json_body(message="请生成方案", paths=[str(source)]),
                content_type="application/json",
                HTTP_IDEMPOTENCY_KEY="unauthorized-conversation",
            )
        self.assertEqual(response.status_code, 404, response.content)
        self.assertFalse(DocumentTask.objects.filter(idempotency_key="unauthorized-conversation").exists())

    def test_task_conversation_appends_message_and_imports_path_without_model_call(self):
        task = self.create_task()
        import_root = Path(self.storage.name) / "approved-imports"
        import_root.mkdir()
        source_path = import_root / "补充需求.txt"
        source_path.write_text("新增要求：保留审计记录。", encoding="utf-8")
        message = f"继续使用 {source_path} 补充技术方案"
        with override_settings(PRODUCT_IMPORT_ROOTS=(import_root,)):
            response = self.owner_client.post(
                f"/api/product/tasks/{task['id']}/conversation/",
                json_body(expected_version=task["version"], message=message),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertFalse(payload["intent"]["model_called"])
        self.assertEqual(payload["intent"]["mode"], "deterministic_fallback")
        self.assertEqual(payload["intent"]["detected_paths"], [str(source_path)])
        self.assertEqual(payload["task"]["sources"][0]["original_name"], source_path.name)
        self.assertIn(message, payload["task"]["input"]["requirements"])
        self.assertEqual(payload["task"]["input"]["conversation_request"]["message"], message)
        self.assertEqual(payload["task"]["input"]["conversation_history"][0]["message"], message)
        self.assertEqual(payload["task"]["input"]["conversation_history"][0]["path_scope"], "shared_authorized_roots")

    def test_task_conversation_is_owner_versioned_and_rolls_back_failed_path_batch(self):
        task = self.create_task()
        reviewer_denied = self.reviewer_client.post(
            f"/api/product/tasks/{task['id']}/conversation/",
            json_body(expected_version=task["version"], message="请继续生成方案"),
            content_type="application/json",
        )
        self.assertEqual(reviewer_denied.status_code, 404, reviewer_denied.content)
        stale = self.owner_client.post(
            f"/api/product/tasks/{task['id']}/conversation/",
            json_body(expected_version=task["version"] + 1, message="请继续生成方案"),
            content_type="application/json",
        )
        self.assertEqual(stale.status_code, 409, stale.content)

        import_root = Path(self.storage.name) / "approved-imports"
        import_root.mkdir()
        valid = import_root / "valid.txt"
        valid.write_text("valid", encoding="utf-8")
        outside = Path(self.storage.name) / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        with override_settings(PRODUCT_IMPORT_ROOTS=(import_root,)):
            failed = self.owner_client.post(
                f"/api/product/tasks/{task['id']}/conversation/",
                json_body(
                    expected_version=task["version"], message="导入补充资料",
                    paths=[str(valid), str(import_root / ".." / outside.name)],
                ),
                content_type="application/json",
            )
        self.assertEqual(failed.status_code, 400, failed.content)
        self.assertEqual(failed.json()["code"], "import_path_not_allowed")
        detail = self.owner_client.get(f"/api/product/tasks/{task['id']}/").json()
        self.assertEqual(detail["version"], task["version"])
        self.assertEqual(detail["sources"], [])
        snapshot_dir = Path(self.storage.name) / task["id"] / "sources"
        self.assertFalse(snapshot_dir.exists() and any(snapshot_dir.iterdir()))

    def create_task(self, *, client=None, key="task-key", reviewer=True):
        client = client or self.owner_client
        payload = {"title": "技术方案", "input": self.input_payload()}
        if reviewer:
            payload["reviewer_id"] = self.reviewer.pk
        response = client.post(
            "/api/product/tasks/",
            json_body(**payload),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=key,
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def blueprint_payload(self):
        return {
            "purpose": "形成技术方案",
            "audience": "项目评审人员",
            "chapters": [{"id": "overview", "title": "项目概述", "scope": "说明范围", "source_ids": ["1"]}],
            "conditions": [{"text": "不得新增清单外设备", "type": "program"}],
            "missing": [],
            "conflicts": [],
            "template_version": "frozen-original-v1",
        }

    def save_blueprint(self, task):
        response = self.owner_client.patch(
            f"/api/product/tasks/{task['id']}/blueprint/",
            json_body(expected_version=task["version"], payload=self.blueprint_payload()),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def approve_blueprint(self, task):
        response = self.owner_client.post(
            f"/api/product/tasks/{task['id']}/decisions/",
            json_body(
                expected_version=task["version"], target="blueprint",
                target_id=task["blueprint"]["id"], sha256=task["blueprint"]["sha256"],
                decision="approve", comment="同意隔离试用",
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    @override_settings(PRODUCT_MODEL_CALLS_ALLOWED=True)
    def test_owner_confirms_current_blueprint_and_queues_all_outputs(self):
        task = self.create_task(reviewer=False)
        current = self.save_blueprint(task)

        response = self.owner_client.post(
            f"/api/product/tasks/{task['id']}/decisions/",
            json_body(
                expected_version=current["version"], target="blueprint",
                target_id=current["blueprint"]["id"], sha256=current["blueprint"]["sha256"],
                decision="approve", comment="确认当前蓝图并生成三件套",
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        result = response.json()["task"]
        self.assertEqual(result["owner_id"], self.owner.pk)
        self.assertIsNone(result["reviewer_id"])
        self.assertEqual(result["state"], "QUEUED")
        self.assertEqual(result["pending_action"], "generate_outputs")

    def test_other_user_cannot_confirm_blueprint_and_owner_replay_is_idempotent(self):
        task = self.create_task(reviewer=False)
        current = self.save_blueprint(task)
        payload = {
            "expected_version": current["version"], "target": "blueprint",
            "target_id": current["blueprint"]["id"], "sha256": current["blueprint"]["sha256"],
            "decision": "approve", "comment": "确认",
        }

        denied = self.other_client.post(
            f"/api/product/tasks/{task['id']}/decisions/", json_body(**payload),
            content_type="application/json",
        )
        first = self.owner_client.post(
            f"/api/product/tasks/{task['id']}/decisions/", json_body(**payload),
            content_type="application/json",
        )
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(first.status_code, 201, first.content)
        payload["expected_version"] = first.json()["task"]["version"]
        replay = self.owner_client.post(
            f"/api/product/tasks/{task['id']}/decisions/", json_body(**payload),
            content_type="application/json",
        )

        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(replay.json()["approval_id"], first.json()["approval_id"])
        self.assertEqual(DocumentApproval.objects.filter(task_id=task["id"], revision__kind="blueprint").count(), 1)

    def test_feature_flag_defaults_to_service_unavailable(self):
        with override_settings(PRODUCT_P1_ENABLED=False):
            response = self.owner_client.get("/api/product/tasks/")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "product_disabled")

    def test_task_ids_are_not_enumerable_and_unknown_fields_are_rejected(self):
        task = self.create_task()

        existing = self.other_client.get(f"/api/product/tasks/{task['id']}/")
        missing = self.other_client.get("/api/product/tasks/00000000-0000-0000-0000-000000000000/")
        forged = self.owner_client.patch(
            f"/api/product/tasks/{task['id']}/",
            json_body(expected_version=task["version"], approved=True, user_id=self.other.pk),
            content_type="application/json",
        )

        self.assertEqual(existing.status_code, 404)
        self.assertEqual(existing.json()["code"], missing.json()["code"])
        self.assertEqual(existing.json()["detail"], missing.json()["detail"])
        self.assertEqual(forged.status_code, 400)
        self.assertEqual(forged.json()["code"], "invalid_request")

    def test_create_is_idempotent_and_conflicting_payload_is_rejected(self):
        first = self.create_task(key="same-key")
        replay = self.owner_client.post(
            "/api/product/tasks/",
            json_body(title="技术方案", input=self.input_payload(), reviewer_id=self.reviewer.pk),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="same-key",
        )
        conflict = self.owner_client.post(
            "/api/product/tasks/",
            json_body(title="不同标题", input=self.input_payload(), reviewer_id=self.reviewer.pk),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="same-key",
        )

        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["id"], first["id"])
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["code"], "idempotency_conflict")

    def test_input_patch_preserves_old_revision_and_rejects_stale_write(self):
        task = self.create_task()
        changed_input = self.input_payload()
        changed_input["items"][0]["quantity"] = 3

        changed = self.owner_client.patch(
            f"/api/product/tasks/{task['id']}/",
            json_body(expected_version=task["version"], input=changed_input),
            content_type="application/json",
        )
        stale = self.owner_client.patch(
            f"/api/product/tasks/{task['id']}/",
            json_body(expected_version=task["version"], title="陈旧修改"),
            content_type="application/json",
        )

        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual(changed.json()["input_version"], 2)
        self.assertEqual(stale.status_code, 409)
        revisions = DocumentRevision.objects.filter(task_id=task["id"], kind="input").order_by("version")
        self.assertEqual(list(revisions.values_list("version", flat=True)), [1, 2])
        self.assertEqual(revisions[0].payload["items"][0]["quantity"], 2)
        self.assertEqual(revisions[1].payload["items"][0]["quantity"], 3)

    def test_non_finite_quantity_is_rejected(self):
        payload = self.input_payload()
        payload["items"][0]["quantity"] = float("nan")

        response = self.owner_client.post(
            "/api/product/tasks/",
            json.dumps({"title": "坏数值", "input": payload}),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="nan-key",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(response.json()["code"], {"invalid_input", "invalid_json"})

    def test_manual_blueprint_preserves_conditions_and_model_queue_blocks(self):
        task = self.create_task()
        empty_payload = self.blueprint_payload()
        empty_payload["chapters"] = []
        empty = self.owner_client.patch(
            f"/api/product/tasks/{task['id']}/blueprint/",
            json_body(expected_version=task["version"], payload=empty_payload),
            content_type="application/json",
        )
        long_title_payload = self.blueprint_payload()
        long_title_payload["chapters"][0]["title"] = "章" * 161
        long_title = self.owner_client.patch(
            f"/api/product/tasks/{task['id']}/blueprint/",
            json_body(expected_version=task["version"], payload=long_title_payload),
            content_type="application/json",
        )
        invalid_payload = self.blueprint_payload()
        invalid_payload["chapters"][0]["source_ids"] = ["not-a-current-reference"]
        invalid = self.owner_client.patch(
            f"/api/product/tasks/{task['id']}/blueprint/",
            json_body(expected_version=task["version"], payload=invalid_payload),
            content_type="application/json",
        )
        saved = self.save_blueprint(task)

        blocked = self.owner_client.post(
            f"/api/product/tasks/{task['id']}/queue/",
            json_body(expected_version=saved["version"], action="blueprint"),
            content_type="application/json",
        )

        self.assertEqual(empty.status_code, 400)
        self.assertEqual(long_title.status_code, 400)
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["code"], "invalid_source")
        self.assertEqual(saved["blueprint"]["payload"]["conditions"][0]["text"], "不得新增清单外设备")
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["code"], "model_authorization_required")
        detail = self.owner_client.get(f"/api/product/tasks/{task['id']}/").json()
        self.assertEqual(detail["state"], "WAITING_INPUT")
        self.assertEqual(detail["error_code"], "model_authorization_required")

    def test_actions_follow_single_owner_blueprint_flow(self):
        task = self.create_task(reviewer=False)
        self.assertIn("queue_retrieve", task["actions"])
        self.assertIn("queue_blueprint", task["actions"])
        self.assertNotIn("confirm_blueprint", task["actions"])
        self.assertNotIn("assign_reviewer", task["actions"])

        self.save_blueprint(task)
        owner_detail = self.owner_client.get(f"/api/product/tasks/{task['id']}/").json()
        self.assertIn("confirm_blueprint", owner_detail["actions"])
        self.assertEqual(self.other_client.get(f"/api/product/tasks/{task['id']}/").status_code, 404)

    def test_blueprint_approval_records_approval_but_does_not_start_worker_when_model_blocked(self):
        task = self.save_blueprint(self.create_task())
        approved = self.approve_blueprint(task)["task"]

        self.assertEqual(approved["state"], "WAITING_INPUT")
        self.assertEqual(approved["error_code"], "model_authorization_required")
        self.assertEqual(DocumentAttempt.objects.filter(task_id=task["id"]).count(), 0)
        self.assertEqual(DocumentApproval.objects.filter(task_id=task["id"], decision="approve").count(), 1)

    def test_owner_confirmation_is_idempotent_and_revocation_invalidates_it(self):
        task = self.save_blueprint(self.create_task(reviewer=False))
        approved = self.approve_blueprint(task)
        replay = self.owner_client.post(
            f"/api/product/tasks/{task['id']}/decisions/",
            json_body(expected_version=approved["task"]["version"], target="blueprint",
                      target_id=task["blueprint"]["id"], sha256=task["blueprint"]["sha256"],
                      decision="approve", comment="重复确认"), content_type="application/json")
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(DocumentApproval.objects.filter(task_id=task["id"]).count(), 1)
        self.owner.roles.clear()
        self.assertIsNone(approved_blueprint(DocumentTask.objects.get(pk=task["id"])))

    def test_revise_supersedes_blueprint_approval_and_old_approve_cannot_replay(self):
        task = self.save_blueprint(self.create_task())
        model_task = DocumentArtifact._meta.apps.get_model("portal", "DocumentTask").objects.get(pk=task["id"])
        blueprint = model_task.revisions.get(kind="blueprint", version=model_task.blueprint_version)
        DocumentApproval.objects.create(
            task=model_task, revision=blueprint, actor=self.owner,
            decision="approve", sha256=blueprint.sha256, authorization=approval_authorization(model_task, self.owner),
        )
        original_fence = model_task.fence
        revised = self.owner_client.post(
            f"/api/product/tasks/{task['id']}/decisions/",
            json_body(
                expected_version=task["version"], target="blueprint", target_id=task["blueprint"]["id"],
                sha256=task["blueprint"]["sha256"], decision="revise", comment="请调整范围",
            ),
            content_type="application/json",
        )
        self.assertEqual(revised.status_code, 201, revised.content)
        model_task.refresh_from_db()
        self.assertEqual(model_task.fence, original_fence + 1)
        self.assertIsNone(model_task.lease_until)
        self.assertIsNone(approved_blueprint(model_task))

        replay = self.owner_client.post(
            f"/api/product/tasks/{task['id']}/decisions/",
            json_body(
                expected_version=task["version"], target="blueprint", target_id=task["blueprint"]["id"],
                sha256=task["blueprint"]["sha256"], decision="approve", comment="重放旧批准",
            ),
            content_type="application/json",
        )
        self.assertEqual(replay.status_code, 409)
        self.assertEqual(replay.json()["code"], "decision_superseded")

    def test_unapproved_template_version_and_malformed_target_uuid_are_safe(self):
        task = self.create_task()
        payload = self.blueprint_payload()
        payload["template_version"] = "unapproved-template"
        invalid_template = self.owner_client.patch(
            f"/api/product/tasks/{task['id']}/blueprint/",
            json_body(expected_version=task["version"], payload=payload),
            content_type="application/json",
        )
        self.assertEqual(invalid_template.status_code, 400)
        self.assertEqual(invalid_template.json()["code"], "invalid_template_version")

        valid = self.save_blueprint(task)
        malformed = self.reviewer_client.post(
            f"/api/product/tasks/{task['id']}/decisions/",
            json_body(
                expected_version=valid["version"], target="blueprint", target_id="not-a-uuid",
                sha256=valid["blueprint"]["sha256"], decision="approve", comment="错误目标",
            ),
            content_type="application/json",
        )
        self.assertEqual(malformed.status_code, 404)
        self.assertEqual(malformed.json()["code"], "not_found")

    def test_decision_requires_waiting_review_stage_and_no_lease(self):
        task = self.save_blueprint(self.create_task())
        model_task = DocumentArtifact._meta.apps.get_model("portal", "DocumentTask").objects.get(pk=task["id"])
        model_task.lease_until = timezone.now() + timedelta(minutes=1)
        model_task.save(update_fields=["lease_until", "updated_at"])

        leased = self.owner_client.post(
            f"/api/product/tasks/{task['id']}/decisions/",
            json_body(
                expected_version=task["version"], target="blueprint", target_id=task["blueprint"]["id"],
                sha256=task["blueprint"]["sha256"], decision="approve", comment="租约期间不可审核",
            ),
            content_type="application/json",
        )
        self.assertEqual(leased.status_code, 409)
        self.assertEqual(leased.json()["code"], "invalid_state")
        self.assertFalse(DocumentApproval.objects.filter(task=model_task).exists())

    def test_cancel_advances_fence_and_clears_lease(self):
        task = self.create_task()
        model_task = DocumentArtifact._meta.apps.get_model("portal", "DocumentTask").objects.get(pk=task["id"])
        model_task.fence = 4
        model_task.lease_until = model_task.updated_at
        model_task.save(update_fields=["fence", "lease_until", "updated_at"])

        response = self.owner_client.post(
            f"/api/product/tasks/{task['id']}/cancel/",
            json_body(expected_version=task["version"]),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        model_task.refresh_from_db()
        self.assertEqual(model_task.state, "CANCELLED")
        self.assertEqual(model_task.fence, 5)
        self.assertIsNone(model_task.lease_until)
        rejected = self.owner_client.patch(
            f"/api/product/tasks/{task['id']}/",
            json_body(expected_version=model_task.version, title="不能恢复"),
            content_type="application/json",
        )
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(rejected.json()["code"], "invalid_state")

    def test_csv_upload_preserves_duplicates_rows_warnings_and_hash_provenance(self):
        task = self.create_task()
        upload = SimpleUploadedFile(
            "设备.csv",
            "序号,设备名称,数量,单位\n1,防火墙,2,台\n1,备用防火墙,1,\n,续行说明,,\n".encode(),
            content_type="application/octet-stream",
        )

        response = self.owner_client.post(
            f"/api/product/tasks/{task['id']}/sources/",
            {"expected_version": task["version"], "file": upload},
        )

        self.assertEqual(response.status_code, 201, response.content)
        detail = response.json()["task"]
        source = detail["sources"][0]
        added = detail["input"]["items"][1:]
        self.assertEqual([item["row_id"] for item in added], ["1", "1", ""])
        self.assertEqual([item["source_row"] for item in added], [2, 3, 4])
        self.assertTrue(all(item["source_id"] == source["id"] for item in added))
        self.assertEqual(detail["input"]["sources"][0]["sha256"], source["sha256"])
        self.assertIn("duplicate_row_id", {warning["code"] for warning in source["warnings"]})
        self.assertIn("missing_unit", {warning["code"] for warning in source["warnings"]})

        editable = {
            key: detail["input"][key]
            for key in ("project", "requirements", "background", "conditions")
        }
        editable["items"] = [
            {key: item[key] for key in ("row_id", "name", "quantity", "unit")}
            for item in detail["input"]["items"]
        ]
        editable["items"][1]["quantity"] = "9"
        updated = self.owner_client.patch(
            f"/api/product/tasks/{task['id']}/",
            json_body(expected_version=detail["version"], input=editable),
            content_type="application/json",
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(updated.json()["input"]["sources"][0]["id"], source["id"])
        self.assertEqual(updated.json()["input"]["items"][1]["source_id"], source["id"])
        self.assertIn("manual_source_row_revision", {issue.get("code") for issue in updated.json()["input"]["issues"]})

    def test_path_upload_and_cross_task_download_are_rejected(self):
        class UnsafeUpload:
            name = "../escape.csv"

            def read(self, size=-1):
                return b"name\nfirewall\n"

        with self.assertRaises(StorageError):
            parse_upload(UnsafeUpload())

        task = self.create_task()
        record = DocumentArtifact._meta.apps.get_model("portal", "DocumentTask").objects.get(pk=task["id"])
        artifact = DocumentArtifact.objects.create(
            task=record, version=1, path="../outside.docx", sha256="0" * 64,
            blueprint_hash="b" * 64, input_hash=record.revisions.get(kind="input", version=record.input_version).sha256, template_hash="t" * 64,
        )
        denied = self.other_client.get(f"/api/product/artifacts/{artifact.pk}/download/")
        traversal = self.owner_client.get(f"/api/product/artifacts/{artifact.pk}/download/?history=1")

        self.assertEqual(denied.status_code, 404)
        self.assertEqual(traversal.status_code, 409)
        self.assertEqual(traversal.json()["code"], "invalid_path")

    def test_download_hash_check_and_formal_approval_gate(self):
        task = self.save_blueprint(self.create_task())
        approved = self.approve_blueprint(task)["task"]
        model_task = DocumentArtifact._meta.apps.get_model("portal", "DocumentTask").objects.get(pk=task["id"])
        input_revision = model_task.revisions.get(kind="input", version=model_task.input_version)
        blueprint_revision = model_task.revisions.get(kind="blueprint", version=model_task.blueprint_version)
        review = DocumentRevision.objects.create(
            task=model_task, kind="review", version=1, payload={"passed": True}, sha256=digest({"passed": True}),
            input_hash=input_revision.sha256, blueprint_hash=blueprint_revision.sha256,
        )
        content = b"draft-document"
        relative = Path(str(model_task.pk)) / "artifacts" / "draft.docx"
        target = Path(self.storage.name) / relative
        target.parent.mkdir(parents=True)
        target.write_bytes(content)
        artifact = DocumentArtifact.objects.create(
            task=model_task, version=1, path=relative.as_posix(), sha256=hashlib.sha256(content).hexdigest(),
            blueprint_hash=blueprint_revision.sha256, input_hash=input_revision.sha256, review=review,
            render_evidence={"status": "verified"}, template_hash="t" * 64,
        )
        model_task.state = "WAITING_REVIEW"
        model_task.stage = "FINAL_REVIEW"
        model_task.save(update_fields=["state", "stage", "updated_at"])

        blocked = self.reviewer_client.post(
            f"/api/product/tasks/{task['id']}/decisions/",
            json_body(
                expected_version=approved["version"], target="artifact", target_id=str(artifact.pk),
                sha256=artifact.sha256, decision="approve", comment="申请正式发布",
            ),
            content_type="application/json",
        )
        self.assertEqual(self.owner_client.get(f"/api/product/artifacts/{artifact.pk}/download/").status_code, 409)
        download = self.owner_client.get(f"/api/product/artifacts/{artifact.pk}/download/?history=1")

        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["code"], "formal_release_blocked")
        self.assertEqual(download.status_code, 200)
        self.assertIn("attachment", download.headers["Content-Disposition"])
        self.assertIn("%E8%8D%89%E7%A8%BF", download.headers["Content-Disposition"])
        self.assertEqual(b"".join(download.streaming_content), content)

    def test_legacy_verified_marker_cannot_replace_real_evidence(self):
        task = self.save_blueprint(self.create_task())
        approved_task = self.approve_blueprint(task)["task"]
        chapter_response = self.owner_client.post(
            f"/api/product/tasks/{task['id']}/chapters/",
            json_body(
                expected_version=approved_task["version"], chapter_id="overview", title="项目概述",
                paragraphs=["设备数量来自当前输入。"], source_ids=["1"],
            ),
            content_type="application/json",
        )
        self.assertEqual(chapter_response.status_code, 201, chapter_response.content)
        chapter = DocumentRevision.objects.get(pk=chapter_response.json()["chapter"]["id"])
        model_task = chapter.task
        blueprint = model_task.revisions.get(kind="blueprint", version=model_task.blueprint_version)
        input_revision = model_task.revisions.get(kind="input", version=model_task.input_version)
        chapter_hashes = {"overview": chapter.sha256}
        review_payload = {"passed": True, "issues": [], "model_review": {"status": "passed", "issues": []}, "chapter_hashes": chapter_hashes}
        review = DocumentRevision.objects.create(
            task=model_task, kind="review", version=1, payload=review_payload, sha256=digest(review_payload),
            input_hash=input_revision.sha256, blueprint_hash=blueprint.sha256,
        )
        content = b"verified-draft"
        relative = Path(str(model_task.pk)) / "artifacts" / "verified.docx"
        target = Path(self.storage.name) / relative
        target.parent.mkdir(parents=True)
        target.write_bytes(content)
        artifact = DocumentArtifact.objects.create(
            task=model_task, version=1, path=relative.as_posix(), sha256=hashlib.sha256(content).hexdigest(),
            blueprint_hash=blueprint.sha256, input_hash=input_revision.sha256, review=review,
            render_evidence={"status": "verified"}, template_hash="t" * 64,
        )
        model_task.state = "WAITING_REVIEW"
        model_task.stage = "FINAL_REVIEW"
        model_task.save(update_fields=["state", "stage", "updated_at"])

        with override_settings(PRODUCT_FORMAL_RELEASE_ENABLED=True):
            approved = self.reviewer_client.post(
                f"/api/product/tasks/{task['id']}/decisions/",
                json_body(
                    expected_version=model_task.version, target="artifact", target_id=str(artifact.pk),
                    sha256=artifact.sha256, decision="approve", comment="正式批准",
                ),
                content_type="application/json",
            )
            self.assertEqual(approved.status_code, 409, approved.content)
            artifact.refresh_from_db()
            self.assertIsNone(effective_artifact_approval(artifact))
            self.assertFalse(self.owner_client.get(f"/api/product/tasks/{task['id']}/").json()["artifacts"][0]["approved"])

        with override_settings(PRODUCT_FORMAL_RELEASE_ENABLED=True, PRODUCT_REVIEWER_IDS=()):
            revoked = self.owner_client.get(f"/api/product/tasks/{task['id']}/")
            self.assertFalse(revoked.json()["artifacts"][0]["approved"])
            draft_download = self.owner_client.get(f"/api/product/artifacts/{artifact.pk}/download/?history=1")
            self.assertIn("%E8%8D%89%E7%A8%BF", draft_download.headers["Content-Disposition"])
            self.assertEqual(b"".join(draft_download.streaming_content), content)

        changed_payload = self.input_payload()
        changed_payload["background"] = "输入已变化"
        changed_input = append_revision(model_task, "input", changed_payload, actor=self.owner)
        model_task.input_version = changed_input.version
        model_task.state = "DRAFT"
        model_task.save(update_fields=["input_version", "state", "updated_at"])
        with override_settings(PRODUCT_FORMAL_RELEASE_ENABLED=True):
            artifact.refresh_from_db()
            self.assertIsNone(effective_artifact_approval(artifact))

    def test_manual_duplicate_row_ids_are_recorded_as_issues(self):
        payload = self.input_payload()
        payload["items"].append({"row_id": "1", "name": "重复设备", "quantity": 1, "unit": "台"})

        response = self.owner_client.post(
            "/api/product/tasks/",
            json_body(title="重复行测试", input=payload),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="duplicate-manual-row",
        )

        self.assertEqual(response.status_code, 201, response.content)
        self.assertIn("duplicate_row_id", {issue["code"] for issue in response.json()["input"]["issues"]})

    def test_manual_chapter_binds_current_input_and_blueprint_hashes(self):
        task = self.save_blueprint(self.create_task())
        approved = self.approve_blueprint(task)["task"]

        empty = self.owner_client.post(
            f"/api/product/tasks/{task['id']}/chapters/",
            json_body(
                expected_version=approved["version"], chapter_id="overview", title="项目概述",
                paragraphs=["   "], source_ids=[],
            ),
            content_type="application/json",
        )
        self.assertEqual(empty.status_code, 400)
        self.assertEqual(empty.json()["code"], "invalid_chapter")

        response = self.owner_client.post(
            f"/api/product/tasks/{task['id']}/chapters/",
            json_body(
                expected_version=approved["version"], chapter_id="overview", title="项目概述",
                paragraphs=["本章仅引用已确认输入。"], source_ids=[],
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        chapter = DocumentRevision.objects.get(pk=response.json()["chapter"]["id"])
        model_task = chapter.task
        self.assertEqual(chapter.input_hash, model_task.revisions.get(kind="input", version=model_task.input_version).sha256)
        self.assertEqual(chapter.blueprint_hash, model_task.revisions.get(kind="blueprint", version=model_task.blueprint_version).sha256)
        self.assertEqual(chapter.payload["paragraphs"], ["本章仅引用已确认输入。"])

    def test_manual_chapter_cannot_expand_blueprint_source_scope(self):
        payload = self.input_payload()
        payload["items"].append({"row_id": "2", "name": "未批准设备", "quantity": 1, "unit": "台"})
        created = self.owner_client.post(
            "/api/product/tasks/",
            json_body(title="来源范围测试", input=payload, reviewer_id=self.reviewer.pk),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="source-scope",
        ).json()
        task = self.save_blueprint(created)
        approved = self.approve_blueprint(task)["task"]

        response = self.owner_client.post(
            f"/api/product/tasks/{task['id']}/chapters/",
            json_body(
                expected_version=approved["version"], chapter_id="overview", title="项目概述",
                paragraphs=["引用未批准范围。"], source_ids=["2"],
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_source_scope")

    @override_settings(
        PRODUCT_MODEL_CALLS_ALLOWED=False,
        PRODUCT_COST_POLICY={},
        PRODUCT_RETRIEVAL_ENABLED=False,
        PRODUCT_RETRIEVAL_AUTHORIZATIONS={},
        PRODUCT_TEMPLATE_APPROVAL={},
        PRODUCT_OFFICE_RENDER_ENABLED=False,
    )
    def test_detail_exposes_configuration_blockers_without_enabling_external_actions(self):
        task = self.create_task(key="public-blockers")
        detail = self.owner_client.get(f"/api/product/tasks/{task['id']}/").json()
        self.assertEqual(detail["blockers"]["queue_retrieve"]["code"], "retrieval_authorization_required")
        self.assertEqual(detail["blockers"]["queue_blueprint"]["code"], "model_authorization_required")
        self.assertFalse(DocumentAttempt.objects.filter(task_id=task["id"]).exists())
