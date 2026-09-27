"""Business ledger workspaces, grants and immutable publication history."""
import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


LEDGER_DEPARTMENTS = (
    ('engineering', '工程'),
    ('finance', '财务'),
    ('presales', '售前'),
)


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


class BusinessLedgerGrant(models.Model):
    """An explicit grant; platform administrators have no implicit ledger access."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='business_ledger_grants')
    department = models.CharField(max_length=16, choices=LEDGER_DEPARTMENTS)
    can_edit = models.BooleanField(default=False)
    can_submit = models.BooleanField(default=False)
    can_publish = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['department', 'user_id']
        constraints = [
            models.UniqueConstraint(fields=['user', 'department'], name='business_grant_user_department_uniq'),
            models.CheckConstraint(
                condition=models.Q(can_edit=True) | models.Q(can_submit=True) | models.Q(can_publish=True),
                name='business_grant_has_capability',
            ),
        ]
        indexes = [models.Index(fields=['user', 'department'], name='business_grant_scope_idx')]

    def clean(self):
        if not any((self.can_edit, self.can_submit, self.can_publish)):
            raise ValidationError('至少授予一项台账权限。')


class BusinessLedgerWorkbook(models.Model):
    class State(models.TextChoices):
        DRAFT = 'draft', '草稿'
        SUBMITTED = 'submitted', '已提交'
        PUBLISHED = 'published', '已发布'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    department = models.CharField(max_length=16, choices=LEDGER_DEPARTMENTS, unique=True)
    state = models.CharField(max_length=16, choices=State, default=State.DRAFT)
    revision = models.PositiveBigIntegerField(default=0)
    source_name = models.CharField(max_length=200, default='手工录入')
    as_of = models.DateField(null=True, blank=True)
    records = models.JSONField(default=list)
    last_return_reason = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_business_ledgers')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='updated_business_ledgers')
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name='submitted_business_ledgers')
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name='published_business_ledgers')
    submitted_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['department']


class BusinessLedgerRevision(models.Model):
    """Append-only snapshots created for every accepted mutation and transition."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workbook = models.ForeignKey(BusinessLedgerWorkbook, on_delete=models.PROTECT, related_name='versions')
    revision = models.PositiveBigIntegerField()
    state = models.CharField(max_length=16, choices=BusinessLedgerWorkbook.State)
    source_name = models.CharField(max_length=200)
    as_of = models.DateField(null=True, blank=True)
    records = models.JSONField(default=list)
    checksum = models.CharField(max_length=64)
    action = models.CharField(max_length=32)
    return_reason = models.CharField(max_length=500, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='business_ledger_versions')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-revision']
        constraints = [
            models.UniqueConstraint(fields=['workbook', 'revision'], name='business_revision_workbook_number_uniq'),
        ]
        indexes = [
            models.Index(fields=['state', '-created_at'], name='business_revision_state_time'),
            models.Index(fields=['workbook', '-revision'], name='business_revision_book_number'),
        ]
