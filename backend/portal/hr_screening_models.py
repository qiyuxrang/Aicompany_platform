import uuid

from django.conf import settings
from django.db import models


class ResumeScreeningBatch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    jd_version = models.ForeignKey('portal.JDVersion', on_delete=models.PROTECT, related_name='screening_batches')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='screening_batches')
    idempotency_key = models.CharField(max_length=128)
    input_version = models.PositiveIntegerField()
    requirements = models.JSONField(default=dict)
    status = models.CharField(max_length=24, default='pending')
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['created_by', 'idempotency_key'], name='hr_batch_owner_key_uq')]

    @property
    def stale(self):
        return self.input_version != self.jd_version.request.input_version or self.jd_version.stale


class ResumeArtifact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(ResumeScreeningBatch, on_delete=models.CASCADE, related_name='artifacts')
    file_id = models.CharField(max_length=36)
    filename = models.CharField(max_length=200)
    sha256 = models.CharField(max_length=64)
    size = models.PositiveIntegerField()
    version = models.PositiveIntegerField(default=1)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    processing_status = models.CharField(max_length=24, default='pending', db_index=True)
    error_code = models.CharField(max_length=64, blank=True)
    extracted_text = models.TextField(blank=True)
    extraction = models.JSONField(default=dict)
    profile = models.JSONField(default=dict)
    match = models.JSONField(default=dict)
    cache_key = models.CharField(max_length=64, blank=True, db_index=True)
    fence = models.PositiveIntegerField(default=0)
    lease_until = models.DateTimeField(null=True, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    attempt_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at', 'id']
        constraints = [models.UniqueConstraint(fields=['batch', 'sha256'], name='hr_batch_resume_hash_uq')]
