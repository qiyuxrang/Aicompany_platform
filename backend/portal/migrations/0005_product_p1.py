import uuid

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("portal", "0004_model_gateway"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentTask",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=200)),
                ("state", models.CharField(choices=[("DRAFT", "草稿"), ("WAITING_INPUT", "等待输入"), ("QUEUED", "已排队"), ("RUNNING", "执行中"), ("WAITING_REVIEW", "等待审核"), ("FAILED", "失败"), ("CANCELLED", "已取消"), ("COMPLETED", "已完成")], default="DRAFT", max_length=20)),
                ("stage", models.CharField(choices=[("INTAKE", "输入"), ("BLUEPRINT", "蓝图"), ("WRITING", "写作"), ("CONTENT_CHECK", "内容检查"), ("RENDER", "渲染"), ("FINAL_REVIEW", "最终审核")], default="INTAKE", max_length=20)),
                ("version", models.PositiveIntegerField(default=1, validators=[django.core.validators.MinValueValidator(1)])),
                ("input_version", models.PositiveIntegerField(default=0)),
                ("blueprint_version", models.PositiveIntegerField(default=0)),
                ("idempotency_key", models.CharField(max_length=128)),
                ("payload_hash", models.CharField(max_length=64)),
                ("fence", models.PositiveIntegerField(default=0)),
                ("lease_until", models.DateTimeField(blank=True, null=True)),
                ("attempt_count", models.PositiveIntegerField(default=0)),
                ("pending_action", models.CharField(blank=True, default="", max_length=32)),
                ("error_code", models.CharField(blank=True, default="", max_length=64)),
                ("checkpoint", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="owned_document_tasks", to=settings.AUTH_USER_MODEL)),
                ("reviewer", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="review_document_tasks", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="DocumentRevision",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("kind", models.CharField(choices=[("input", "输入"), ("blueprint", "蓝图"), ("chapter", "章节"), ("review", "审查")], max_length=16)),
                ("version", models.PositiveIntegerField(validators=[django.core.validators.MinValueValidator(1)])),
                ("payload", models.JSONField()),
                ("sha256", models.CharField(max_length=64)),
                ("input_hash", models.CharField(blank=True, default="", max_length=64)),
                ("blueprint_hash", models.CharField(blank=True, default="", max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="document_revisions", to=settings.AUTH_USER_MODEL)),
                ("task", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="revisions", to="portal.documenttask")),
            ],
        ),
        migrations.CreateModel(
            name="DocumentSource",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("original_name", models.CharField(max_length=255)),
                ("media_type", models.CharField(max_length=100)),
                ("path", models.CharField(max_length=500)),
                ("sha256", models.CharField(max_length=64)),
                ("size", models.PositiveBigIntegerField()),
                ("parsed", models.JSONField(default=dict)),
                ("warnings", models.JSONField(default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("task", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sources", to="portal.documenttask")),
            ],
        ),
        migrations.CreateModel(
            name="DocumentAttempt",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("fence", models.PositiveIntegerField(validators=[django.core.validators.MinValueValidator(1)])),
                ("action", models.CharField(max_length=32)),
                ("start_at", models.DateTimeField(auto_now_add=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("status", models.CharField(choices=[("running", "执行中"), ("done", "完成"), ("failed", "失败"), ("cancelled", "已取消")], default="running", max_length=16)),
                ("model_calls", models.PositiveIntegerField(default=0)),
                ("error_code", models.CharField(blank=True, default="", max_length=64)),
                ("task", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="attempts", to="portal.documenttask")),
            ],
        ),
        migrations.CreateModel(
            name="DocumentArtifact",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("version", models.PositiveIntegerField(validators=[django.core.validators.MinValueValidator(1)])),
                ("path", models.CharField(max_length=500)),
                ("sha256", models.CharField(max_length=64)),
                ("blueprint_hash", models.CharField(max_length=64)),
                ("input_hash", models.CharField(max_length=64)),
                ("render_evidence", models.JSONField(default=dict)),
                ("template_hash", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("review", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="artifacts", to="portal.documentrevision")),
                ("task", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="artifacts", to="portal.documenttask")),
            ],
        ),
        migrations.CreateModel(
            name="DocumentApproval",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("decision", models.CharField(choices=[("approve", "批准"), ("revise", "退回修改")], max_length=16)),
                ("comment", models.TextField(blank=True)),
                ("sha256", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="document_approvals", to=settings.AUTH_USER_MODEL)),
                ("artifact", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="approvals", to="portal.documentartifact")),
                ("revision", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="approvals", to="portal.documentrevision")),
                ("task", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="approvals", to="portal.documenttask")),
            ],
        ),
        migrations.AddConstraint(model_name="documenttask", constraint=models.UniqueConstraint(fields=("owner", "idempotency_key"), name="product_task_owner_idem_uq")),
        migrations.AddConstraint(model_name="documenttask", constraint=models.CheckConstraint(condition=models.Q(("version__gt", 0)), name="product_task_version_gt0_ck")),
        migrations.AddConstraint(model_name="documentrevision", constraint=models.UniqueConstraint(fields=("task", "kind", "version"), name="product_revision_kind_ver_uq")),
        migrations.AddConstraint(model_name="documentrevision", constraint=models.CheckConstraint(condition=models.Q(("version__gt", 0)), name="product_revision_ver_gt0_ck")),
        migrations.AddConstraint(model_name="documentattempt", constraint=models.UniqueConstraint(fields=("task", "fence"), name="product_attempt_fence_uq")),
        migrations.AddConstraint(model_name="documentattempt", constraint=models.CheckConstraint(condition=models.Q(("fence__gt", 0)), name="product_attempt_fence_gt0_ck")),
        migrations.AddConstraint(model_name="documentartifact", constraint=models.UniqueConstraint(fields=("task", "version"), name="product_artifact_ver_uq")),
        migrations.AddConstraint(model_name="documentartifact", constraint=models.CheckConstraint(condition=models.Q(("version__gt", 0)), name="product_artifact_ver_gt0_ck")),
        migrations.AddConstraint(model_name="documentapproval", constraint=models.CheckConstraint(condition=models.Q(models.Q(("artifact__isnull", True), ("revision__isnull", False)), models.Q(("artifact__isnull", False), ("revision__isnull", True)), _connector="OR"), name="product_approval_one_target_ck")),
        migrations.AddConstraint(model_name="documentapproval", constraint=models.UniqueConstraint(condition=models.Q(("revision__isnull", False)), fields=("revision", "actor", "decision"), name="product_approval_revision_uq")),
        migrations.AddConstraint(model_name="documentapproval", constraint=models.UniqueConstraint(condition=models.Q(("artifact__isnull", False)), fields=("artifact", "actor", "decision"), name="product_approval_artifact_uq")),
    ]
