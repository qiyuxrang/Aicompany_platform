from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models, transaction


def validate_module_url(value):
    if not value:
        return
    try:
        parsed = urlsplit(value)
        parsed.port
    except (ValueError, TypeError) as error:
        raise ValidationError("入口地址格式无效。") from error
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if (parsed.scheme not in ("https", "http") or parsed.username or parsed.password
            or parsed.fragment or parsed.query or "\\" in value or any(ord(char) < 33 for char in value)
            or origin not in settings.TRUSTED_MODULE_ORIGINS):
        raise ValidationError("入口必须使用可信 HTTP(S) 目标，不得包含凭据、查询参数或片段。")
    if not settings.DEBUG and parsed.scheme != "https":
        raise ValidationError("正式配置只允许 HTTPS 入口。")


class Module(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "待接入"
        NAVIGATION = "navigation", "导航接入"
        VERIFIED = "verified", "已验证集成"
    code = models.SlugField(unique=True)
    name = models.CharField("名称", max_length=80)
    description = models.CharField("说明", max_length=300, blank=True)
    status = models.CharField("接入状态", max_length=20, choices=Status, default=Status.PENDING)
    enabled = models.BooleanField("启用", default=True)
    url = models.URLField("入口地址", max_length=500, blank=True, validators=[validate_module_url])

    def clean(self):
        if self.status != self.Status.PENDING and not self.url:
            raise ValidationError("已接入模块必须配置可信入口地址。")

    def __str__(self):
        return self.name


class ModuleCheck(models.Model):
    class State(models.TextChoices):
        REACHABLE = "reachable", "可访问"
        UNAVAILABLE = "unavailable", "不可访问"
        NOT_CONFIGURED = "not_configured", "未配置"
        DISABLED = "disabled", "已停用"
        ERROR = "error", "检查异常"

    module = models.OneToOneField(Module, primary_key=True, on_delete=models.CASCADE, related_name="latest_check")
    state = models.CharField(max_length=20, choices=State)
    checked_at = models.DateTimeField()
    next_check_at = models.DateTimeField()
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    message = models.CharField(max_length=200)
    config_digest = models.CharField(max_length=64)


class OperationalIssue(models.Model):
    class Severity(models.TextChoices):
        WARNING = "warning", "警告"
        CRITICAL = "critical", "严重"

    class Status(models.TextChoices):
        OPEN = "open", "待处理"
        INVESTIGATING = "investigating", "处理中"
        CLOSED = "closed", "已人工关闭"
        RECOVERED = "recovered", "已恢复"

    module = models.OneToOneField(Module, on_delete=models.CASCADE, related_name="operational_issue")
    severity = models.CharField(max_length=20, choices=Severity)
    title = models.CharField(max_length=120)
    status = models.CharField(max_length=20, choices=Status, default=Status.OPEN)
    first_seen = models.DateTimeField()
    last_seen = models.DateTimeField()
    occurrences = models.PositiveIntegerField(default=1)
    evidence = models.CharField(max_length=200)
    checked_at = models.DateTimeField()
    notes = models.JSONField(default=list)
    closed_at = models.DateTimeField(null=True, blank=True)
    recovered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_seen", "-id"]
        indexes = [
            models.Index(fields=["status", "last_seen"], name="portal_issue_status_time"),
            models.Index(fields=["severity", "last_seen"], name="portal_issue_sev_time"),
        ]

    @property
    def health_state(self):
        return "recovered" if self.recovered_at and self.recovered_at >= self.last_seen else "unresolved"


class Role(models.Model):
    code = models.SlugField(unique=True)
    name = models.CharField("角色", max_length=80)
    modules = models.ManyToManyField(Module, blank=True, verbose_name="模块授权")

    def __str__(self):
        return self.name


class User(AbstractUser):
    security_fields = ("password", "is_active", "must_change_password")
    display_name = models.CharField("显示名称", max_length=80, blank=True)
    roles = models.ManyToManyField(Role, blank=True, verbose_name="角色")
    must_change_password = models.BooleanField(default=True)
    session_version = models.PositiveIntegerField(default=1)
    grant_version = models.PositiveBigIntegerField(default=1)

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._loaded_security = {field: instance.__dict__[field] for field in cls.security_fields if field in instance.__dict__}
        return instance

    def refresh_from_db(self, *args, **kwargs):
        super().refresh_from_db(*args, **kwargs)
        self._loaded_security = {field: self.__dict__[field] for field in self.security_fields if field in self.__dict__}

    @transaction.atomic
    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            fields = kwargs.get("update_fields")
            if fields is None:
                fields = {field.name for field in self._meta.concrete_fields if not field.primary_key and field.attname in self.__dict__}
                loaded = getattr(self, "_loaded_security", {})
                for field in self.security_fields:
                    if field not in loaded or self.__dict__.get(field) == loaded[field]:
                        fields.discard(field)
            else:
                fields = set(fields)
            fields.discard("grant_version")
            fields.discard("session_version")
            previous = type(self).objects.select_for_update().filter(pk=self.pk).values(*self.security_fields, "session_version").first()
            if previous:
                changed = any(field in fields and previous[field] != getattr(self, field) for field in ("is_active", "password"))
                self.session_version = previous["session_version"] + int(changed)
                if changed:
                    fields.add("session_version")
                for field in self.security_fields:
                    if field not in fields:
                        setattr(self, field, previous[field])
            kwargs["update_fields"] = fields
        result = super().save(*args, **kwargs)
        self._loaded_security = {field: self.__dict__[field] for field in self.security_fields if field in self.__dict__}
        return result

    @property
    def is_platform_admin(self):
        return self.is_active and self.roles.filter(code="platform_admin").exists()


class AuditEvent(models.Model):
    created_at = models.DateTimeField("时间", auto_now_add=True)
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, verbose_name="操作者")
    action = models.CharField("动作", max_length=80)
    target = models.CharField("对象", max_length=150, blank=True)
    result = models.CharField("结果", max_length=30, default="success")
    changes = models.JSONField("变更字段（不含值）", default=list)

    class Meta:
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["action", "result", "created_at"], name="portal_audit_act_res_time"),
            models.Index(fields=["actor", "created_at"], name="portal_audit_actor_time"),
        ]


class LoginAttempt(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    started_at = models.DateTimeField()
    count = models.PositiveIntegerField(default=0)


class BusinessMapping(models.Model):
    user = models.OneToOneField(User, on_delete=models.PROTECT, verbose_name="平台用户")
    external_user_id = models.CharField("旧系统用户标识", max_length=100, unique=True)
    enabled = models.BooleanField("启用", default=True)

    def __str__(self):
        return f"映射 #{self.pk}"


class IntegrationTicket(models.Model):
    digest = models.CharField(max_length=64, unique=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    mapping = models.ForeignKey(BusinessMapping, on_delete=models.CASCADE)
    external_user_id = models.CharField(max_length=100)
    session_version = models.PositiveIntegerField()
    grant_version = models.PositiveBigIntegerField(default=1)
    audience = models.CharField(max_length=50, default="business")
    purpose = models.CharField(max_length=50, default="read_summary")
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True)


from .model_config import GatewayModel, ModelCallLog, ModelRoute, Provider
from .product_models import (DocumentApproval, DocumentArtifact, DocumentAttempt,
                             DocumentRevision, DocumentSource, DocumentTask, DocumentReviewPolicy)
from .hr_models import HrJobRevision, HrJobTask, ProbationCase, ProbationTransition
