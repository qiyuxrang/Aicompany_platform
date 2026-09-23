import uuid

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q


class DocumentTask(models.Model):
    class State(models.TextChoices):
        DRAFT = "DRAFT", "草稿"
        WAITING_INPUT = "WAITING_INPUT", "等待输入"
        QUEUED = "QUEUED", "已排队"
        RUNNING = "RUNNING", "执行中"
        WAITING_REVIEW = "WAITING_REVIEW", "等待审核"
        FAILED = "FAILED", "失败"
        CANCELLED = "CANCELLED", "已取消"
        COMPLETED = "COMPLETED", "已完成"

    class Stage(models.TextChoices):
        INTAKE = "INTAKE", "输入"
        BLUEPRINT = "BLUEPRINT", "蓝图"
        WRITING = "WRITING", "写作"
        CONTENT_CHECK = "CONTENT_CHECK", "内容检查"
        RENDER = "RENDER", "渲染"
        FINAL_REVIEW = "FINAL_REVIEW", "最终审核"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owned_document_tasks")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="review_document_tasks", null=True, blank=True)
    title = models.CharField(max_length=200)
    state = models.CharField(max_length=20, choices=State, default=State.DRAFT)
    stage = models.CharField(max_length=20, choices=Stage, default=Stage.INTAKE)
    version = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    input_version = models.PositiveIntegerField(default=0)
    blueprint_version = models.PositiveIntegerField(default=0)
    idempotency_key = models.CharField(max_length=128)
    payload_hash = models.CharField(max_length=64)
    fence = models.PositiveIntegerField(default=0)
    lease_until = models.DateTimeField(null=True, blank=True)
    attempt_count = models.PositiveIntegerField(default=0)
    pending_action = models.CharField(max_length=32, default="", blank=True)
    error_code = models.CharField(max_length=64, default="", blank=True)
    checkpoint = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["owner", "idempotency_key"], name="product_task_owner_idem_uq"),
            models.CheckConstraint(condition=Q(version__gt=0), name="product_task_version_gt0_ck"),
        ]


class DocumentRevision(models.Model):
    class Kind(models.TextChoices):
        INPUT = "input", "输入"
        BLUEPRINT = "blueprint", "蓝图"
        CHAPTER = "chapter", "章节"
        REVIEW = "review", "审查"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(DocumentTask, on_delete=models.CASCADE, related_name="revisions")
    kind = models.CharField(max_length=16, choices=Kind)
    version = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    payload = models.JSONField()
    sha256 = models.CharField(max_length=64)
    input_hash = models.CharField(max_length=64, default="", blank=True)
    blueprint_hash = models.CharField(max_length=64, default="", blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="document_revisions")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["task", "kind", "version"], name="product_revision_kind_ver_uq"),
            models.CheckConstraint(condition=Q(version__gt=0), name="product_revision_ver_gt0_ck"),
        ]


class DocumentSource(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(DocumentTask, on_delete=models.CASCADE, related_name="sources")
    original_name = models.CharField(max_length=255)
    media_type = models.CharField(max_length=100)
    path = models.CharField(max_length=500)
    sha256 = models.CharField(max_length=64)
    size = models.PositiveBigIntegerField()
    parsed = models.JSONField(default=dict)
    warnings = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)


class DocumentAttempt(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "执行中"
        DONE = "done", "完成"
        FAILED = "failed", "失败"
        CANCELLED = "cancelled", "已取消"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(DocumentTask, on_delete=models.CASCADE, related_name="attempts")
    fence = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    action = models.CharField(max_length=32)
    start_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status, default=Status.RUNNING)
    model_calls = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=64, default="", blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["task", "fence"], name="product_attempt_fence_uq"),
            models.CheckConstraint(condition=Q(fence__gt=0), name="product_attempt_fence_gt0_ck"),
        ]


class DocumentArtifact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(DocumentTask, on_delete=models.CASCADE, related_name="artifacts")
    version = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    path = models.CharField(max_length=500)
    sha256 = models.CharField(max_length=64)
    blueprint_hash = models.CharField(max_length=64)
    input_hash = models.CharField(max_length=64)
    review = models.ForeignKey(DocumentRevision, on_delete=models.PROTECT, null=True, blank=True, related_name="artifacts")
    render_evidence = models.JSONField(default=dict)
    template_hash = models.CharField(max_length=64)
    generation_hash = models.CharField(max_length=64, default="", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["task", "version"], name="product_artifact_ver_uq"),
            models.UniqueConstraint(fields=["task", "generation_hash"], condition=~Q(generation_hash=""), name="product_artifact_generation_uq"),
            models.CheckConstraint(condition=Q(version__gt=0), name="product_artifact_ver_gt0_ck"),
        ]


class DocumentApproval(models.Model):
    class Decision(models.TextChoices):
        APPROVE = "approve", "批准"
        REVISE = "revise", "退回修改"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(DocumentTask, on_delete=models.CASCADE, related_name="approvals")
    revision = models.ForeignKey(DocumentRevision, on_delete=models.PROTECT, null=True, blank=True, related_name="approvals")
    artifact = models.ForeignKey(DocumentArtifact, on_delete=models.PROTECT, null=True, blank=True, related_name="approvals")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="document_approvals")
    decision = models.CharField(max_length=16, choices=Decision)
    authorization = models.JSONField(default=dict)
    comment = models.TextField(blank=True)
    sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(Q(revision__isnull=False, artifact__isnull=True) | Q(revision__isnull=True, artifact__isnull=False)),
                name="product_approval_one_target_ck",
            ),
            models.UniqueConstraint(
                fields=["revision", "actor", "decision"],
                condition=Q(revision__isnull=False),
                name="product_approval_revision_uq",
            ),
            models.UniqueConstraint(
                fields=["artifact", "actor", "decision"],
                condition=Q(artifact__isnull=False),
                name="product_approval_artifact_uq",
            ),
        ]


class DocumentReviewPolicy(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    fingerprint = models.CharField(max_length=64)
    version = models.PositiveBigIntegerField(default=1)
