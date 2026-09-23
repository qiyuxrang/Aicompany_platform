import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class HrJobTask(models.Model):
    class State(models.TextChoices):
        DRAFT = "draft", "草稿"
        GENERATED = "generated", "已生成"
        CONFIRMED = "confirmed", "已确认"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="hr_job_tasks")
    title = models.CharField(max_length=200)
    department = models.CharField(max_length=200, blank=True)
    objective = models.TextField(blank=True)
    responsibilities = models.TextField(blank=True)
    requirements = models.TextField(blank=True)
    state = models.CharField(max_length=16, choices=State, default=State.DRAFT)
    version = models.PositiveIntegerField(default=1)
    input_version = models.PositiveIntegerField(default=1)
    current_revision = models.ForeignKey("HrJobRevision", null=True, blank=True, on_delete=models.SET_NULL, related_name="current_for_tasks")
    official_revision = models.ForeignKey("HrJobRevision", null=True, blank=True, on_delete=models.PROTECT, related_name="official_for_tasks")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(version__gt=0), name="hr_job_version_gt0_ck"),
            models.CheckConstraint(condition=Q(input_version__gt=0), name="hr_job_input_ver_gt0_ck"),
        ]


class HrJobRevision(models.Model):
    class Kind(models.TextChoices):
        GENERATED = "generated", "确定性草稿"
        MANUAL = "manual", "人工修改"
        CONFIRMED = "confirmed", "HR确认"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(HrJobTask, on_delete=models.CASCADE, related_name="revisions")
    version = models.PositiveIntegerField()
    input_version = models.PositiveIntegerField()
    kind = models.CharField(max_length=16, choices=Kind)
    body = models.TextField()
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="hr_job_revisions")
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="confirmed_hr_job_revisions")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["version"]
        constraints = [
            models.UniqueConstraint(fields=["task", "version"], name="hr_job_revision_ver_uq"),
            models.CheckConstraint(condition=Q(version__gt=0), name="hr_job_revision_ver_gt0_ck"),
            models.CheckConstraint(condition=Q(input_version__gt=0), name="hr_job_revision_input_gt0_ck"),
        ]


class ProbationCase(models.Model):
    class State(models.TextChoices):
        DRAFT = "draft", "草稿"
        COLLECTING = "collecting", "材料收集中"
        MANAGER_PENDING = "manager_pending", "待主管审批"
        HR_PENDING = "hr_pending", "待HR确认"
        ARCHIVED = "archived", "已归档"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owned_probation_cases")
    assigned_manager = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="managed_probation_cases")
    employee_name = models.CharField(max_length=200)
    position = models.CharField(max_length=200)
    materials = models.JSONField(default=list)
    notes = models.TextField(blank=True)
    manager_opinion = models.TextField(blank=True)
    hr_conclusion = models.TextField(blank=True)
    assistant_mode = models.CharField(max_length=16, default="manual")
    assistant_reason = models.CharField(max_length=64, default="model_not_authorized")
    state = models.CharField(max_length=24, choices=State, default=State.DRAFT)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-created_at"]
        constraints = [models.CheckConstraint(condition=Q(version__gt=0), name="hr_probation_version_gt0_ck")]


class ProbationRevision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(ProbationCase, on_delete=models.CASCADE, related_name="revisions")
    before = models.JSONField()
    after = models.JSONField()
    changed_fields = models.JSONField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="probation_revisions")
    case_version = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [models.CheckConstraint(condition=Q(case_version__gt=0), name="hr_probation_revision_ver_gt0_ck")]


class ProbationTransition(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(ProbationCase, on_delete=models.CASCADE, related_name="transitions")
    from_state = models.CharField(max_length=24, choices=ProbationCase.State)
    to_state = models.CharField(max_length=24, choices=ProbationCase.State)
    action = models.CharField(max_length=32)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="probation_transitions")
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
