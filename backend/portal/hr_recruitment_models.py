"""Platform-owned recruitment requirements; standalone HR data is not imported."""
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class RecruitmentRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    position_name = models.CharField(max_length=200, blank=True)
    headcount = models.PositiveIntegerField(null=True, blank=True)
    responsibilities = models.TextField(blank=True)
    required_requirements = models.TextField(blank=True)
    preferred_requirements = models.TextField(blank=True)
    education_requirement = models.CharField(max_length=200, blank=True)
    experience_requirement = models.CharField(max_length=200, blank=True)
    skill_requirements = models.JSONField(default=list)
    work_location = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)
    input_version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name='recruitment_requests_created')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name='recruitment_requests_updated')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-created_at']
        constraints = [
            models.CheckConstraint(condition=Q(headcount__isnull=True) | Q(headcount__gt=0),
                                   name='hr_req_headcount_gt0_ck'),
            models.CheckConstraint(condition=Q(input_version__gt=0), name='hr_req_input_version_gt0_ck'),
        ]

    def structured_payload(self):
        from .hr_recruitment_service import REQUEST_FIELDS
        return {field: getattr(self, field) for field in sorted(REQUEST_FIELDS)}
