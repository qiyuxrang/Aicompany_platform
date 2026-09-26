"""Read-only ledger snapshots; never write back to the original business system."""
import uuid
from django.conf import settings
from django.db import models


class BusinessLedgerSnapshot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='ledger_snapshots')
    department = models.CharField(max_length=16)
    source_name = models.CharField(max_length=200)
    as_of = models.DateField()
    records = models.JSONField(default=list)
    checksum = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [models.Index(fields=['owner', 'department', '-created_at'], name='business_snapshot_scope_idx')]
