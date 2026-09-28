import uuid

from django.conf import settings
from django.db import models


class EngineeringJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "排队中"
        RUNNING = "running", "处理中"
        COMPLETED = "completed", "已完成"
        FAILED = "failed", "失败"
        BLOCKED = "blocked", "阻塞"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                              related_name="engineering_jobs")
    region = models.CharField(max_length=80, default="陕西")
    inputs = models.JSONField(default=list)
    inspection = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    result_path = models.CharField(max_length=500, blank=True)
    result_filename = models.CharField(max_length=200, blank=True)
    result_sha256 = models.CharField(max_length=64, blank=True)
    result_size = models.PositiveBigIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status, default=Status.QUEUED,
                              db_index=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_detail = models.CharField(max_length=300, blank=True)
    attempt_count = models.PositiveIntegerField(default=0)
    fence = models.PositiveIntegerField(default=0)
    lease_until = models.DateTimeField(null=True, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "next_retry_at", "created_at"],
                         name="portal_eng_status_retry"),
        ]
