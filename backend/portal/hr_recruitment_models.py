"""Platform-owned recruitment requirements; standalone HR data is not imported."""
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class RecruitmentRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    archive_state = models.CharField(max_length=16, default='active')
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
    salary = models.TextField(blank=True)
    benefits = models.TextField(blank=True)
    social_insurance = models.TextField(blank=True)
    original_text = models.TextField(blank=True)
    intake_source = models.CharField(max_length=16, default='structured')
    input_version = models.PositiveIntegerField(default=1)
    current_jd = models.ForeignKey('JDVersion', null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name='current_for_requests')
    official_jd = models.ForeignKey('JDVersion', null=True, blank=True, on_delete=models.PROTECT,
                                   related_name='official_for_requests')
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


class JDVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(RecruitmentRequest, on_delete=models.CASCADE, related_name='jd_versions')
    version = models.PositiveIntegerField()
    input_version = models.PositiveIntegerField()
    state = models.CharField(max_length=16, default='draft', choices=[('draft', '草稿'), ('confirmed', '已确认')])
    source = models.CharField(max_length=16, default='skill')
    body = models.TextField()
    requirements = models.JSONField(default=dict)
    channel = models.CharField(max_length=20, default='general')
    custom_label = models.CharField(max_length=100, blank=True)
    model_selection = models.JSONField(default=dict, blank=True)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='children')
    source_jd = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='channel_versions')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='jd_versions_created')
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name='jd_versions_confirmed')
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['version']
        constraints = [
            models.UniqueConstraint(fields=['request', 'version'], name='hr_jd_version_uq'),
            models.CheckConstraint(condition=Q(version__gt=0), name='hr_jd_version_gt0_ck'),
            models.CheckConstraint(condition=Q(input_version__gt=0), name='hr_jd_input_version_gt0_ck'),
        ]

    @property
    def stale(self):
        return (self.input_version != self.request.input_version
                or bool(self.source_jd_id and self.source_jd_id != self.request.official_jd_id))


class RecruitmentMessage(models.Model):
    request = models.ForeignKey(RecruitmentRequest, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=16, choices=[('user', '用户'), ('assistant', '助手')])
    content = models.TextField()
    input_version = models.PositiveIntegerField()
    jd_version = models.ForeignKey(JDVersion, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name='messages')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']
