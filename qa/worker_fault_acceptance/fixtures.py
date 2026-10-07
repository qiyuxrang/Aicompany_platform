"""Synthetic business fixtures and semantic snapshots using actual Portal models."""
import hashlib
import json
import secrets

class Fixtures:
    def __init__(self, token, gateway):
        from django.core.management import call_command
        from django.contrib.auth.password_validation import validate_password
        from portal.models import User, Role, Module
        from portal.model_config import Provider, GatewayModel, ModelRoute
        call_command("seed_portal", verbosity=0)
        self.token = token
        password = secrets.token_urlsafe(32)
        validate_password(password)
        self.hr = User.objects.create_user(username="synthetic-fault-hr", password=password, department_code="hr", must_change_password=False)
        self.hr.roles.set(Role.objects.filter(code="hr"))
        self.product = User.objects.create_user(username="synthetic-fault-product", password=secrets.token_urlsafe(32), department_code="product", must_change_password=False)
        self.product.roles.set(Role.objects.filter(code="product"))
        provider = Provider.objects.create(code="fault-mock-only", name="Synthetic loopback; never provider", base_url="https://synthetic.invalid/v1", api_key_env="PORTAL_MODEL_KEY_FAULT_SYNTHETIC_NEVER_PROVIDER", enabled=True)
        for route, department in (("hr_resume_parse", "hr"), ("hr_match_summary", "hr"), ("product_blueprint", "product")):
            model = GatewayModel.objects.create(name="Synthetic " + route, provider=provider, model_name=route, enabled=True, timeout_seconds=15)
            ModelRoute.objects.update_or_create(code=route, defaults={"name": "Synthetic " + route, "module": Module.objects.get(code=department), "model": model, "enabled": True, "max_calls_per_minute": 120})
    def create(self, kind, key):
        if kind == "hr":
            from portal.hr_recruitment_models import RecruitmentRequest, JDVersion
            from portal.hr_screening_models import ResumeScreeningBatch, ResumeArtifact
            from portal.hr_resume_storage import save_file
            req = RecruitmentRequest.objects.create(created_by=self.hr, updated_by=self.hr, position_name="隔离故障岗位", skill_requirements=["SQL"])
            jd = JDVersion.objects.create(request=req, version=1, input_version=1, state="confirmed", body="合成岗位需要SQL", created_by=self.hr)
            req.current_jd = req.official_jd = jd
            req.save()
            batch = ResumeScreeningBatch.objects.create(jd_version=jd, created_by=self.hr, input_version=1, requirements=req.structured_payload(), idempotency_key=key, status="queued")
            return ResumeArtifact.objects.create(batch=batch, uploaded_by=self.hr, processing_status="queued", **save_file(key + ".txt", ("姓名：合成人\n技能：SQL\n样例：" + key).encode()))
        from portal.product_models import DocumentTask
        from portal.product_service import append_revision, digest
        payload = {"project": "隔离故障方案", "requirements": "不新增清单外设备", "background": "仅验证恢复机制", "items": [{"row_id": "r1", "name": "测试设备", "quantity": "2", "unit": "台"}], "conditions": ["不新增清单外设备"]}
        task = DocumentTask.objects.create(owner=self.product, title="合成故障蓝图", idempotency_key=key, payload_hash=digest(payload), state="QUEUED", stage="BLUEPRINT", pending_action="blueprint")
        rev = append_revision(task, "input", payload, actor=self.product)
        task.input_version = rev.version
        task.save(update_fields=["input_version"])
        return task
    def committed(self, kind, row):
        row.refresh_from_db()
        if kind == "hr":
            row.batch.refresh_from_db()
            return row.processing_status == "completed" and row.match.get("score", {}).get("total") == 1 and row.batch.status == "completed"
        from portal.product_models import DocumentRevision, DocumentArtifact
        return row.state == "WAITING_REVIEW" and DocumentRevision.objects.filter(task=row, kind="blueprint").count() == 1 and not DocumentArtifact.objects.filter(task=row).exists()
    def snapshot(self, kind, row):
        from portal.models import AuditEvent
        row.refresh_from_db()
        if kind == "hr":
            row.batch.refresh_from_db()
            data = {"state": row.processing_status, "fence": row.fence, "attempts": row.attempt_count, "version": row.version, "batch_state": row.batch.status, "batch_version": row.batch.version, "match_sha256": hashlib.sha256(json.dumps(row.match, sort_keys=True).encode()).hexdigest(), "audits": AuditEvent.objects.filter(target=str(row.pk), action="hr_resume_process").count()}
        else:
            from portal.product_models import DocumentRevision, DocumentAttempt, DocumentArtifact
            data = {"state": row.state, "fence": row.fence, "version": row.version, "attempts": row.attempt_count, "checkpoint_sha256": hashlib.sha256(json.dumps(row.checkpoint, sort_keys=True).encode()).hexdigest(), "revisions": list(DocumentRevision.objects.filter(task=row).order_by("kind", "version").values_list("kind", "version", "sha256")), "artifact_count": DocumentArtifact.objects.filter(task=row).count(), "attempt_rows": DocumentAttempt.objects.filter(task=row).count(), "audits": AuditEvent.objects.filter(target__startswith=str(row.pk)).count()}
        return data
    def reject_stale(self, kind, row, old_fence):
        if kind == "hr":
            from portal.hr_screening_worker import finish_one
            return finish_one(row.pk, old_fence, error="synthetic_late_holder") is False
        from portal.product_worker import _store, ExecutionError
        try:
            _store(row.pk, old_fence, "blueprint", {}, "0"*64)
        except ExecutionError as error:
            return error.code == "lease_lost"
        return False
