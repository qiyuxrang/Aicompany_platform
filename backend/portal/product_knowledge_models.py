"""Local, owner-private knowledge conversations (import from portal.models at integration)."""
import uuid
from django.conf import settings
from django.db import models


class ProductKnowledgeConversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=100, default="新对话")
    scope = models.JSONField(default=dict)
    turns = models.JSONField(default=list)
    version = models.PositiveIntegerField(default=0)
    pending_id = models.UUIDField(null=True, blank=True)
    pending_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "portal"
        ordering = ["-updated_at", "id"]
