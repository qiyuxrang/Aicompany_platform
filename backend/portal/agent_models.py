import uuid

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F


def identifier():
    return uuid.uuid4()


class AgentProject(models.Model):
    id = models.UUIDField(primary_key=True, default=identifier, editable=False)
    owner = models.ForeignKey("portal.User", on_delete=models.PROTECT)
    department_code = models.CharField(max_length=20)
    name = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class AgentAttachment(models.Model):
    id = models.UUIDField(primary_key=True, default=identifier, editable=False)
    owner = models.ForeignKey("portal.User", on_delete=models.PROTECT)
    department_code = models.CharField(max_length=20, blank=True)
    name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    size = models.PositiveBigIntegerField()
    path = models.CharField(max_length=500)
    sha256 = models.CharField(max_length=64)
    source_scope = models.CharField(max_length=20, default="private")
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class AgentConversation(models.Model):
    id = models.UUIDField(primary_key=True, default=identifier, editable=False)
    owner = models.ForeignKey("portal.User", on_delete=models.PROTECT)
    department_code = models.CharField(max_length=20, blank=True)
    project = models.ForeignKey(AgentProject, null=True, blank=True, on_delete=models.PROTECT)
    root_run_id = models.UUIDField(default=identifier, unique=True, editable=False)
    create_key = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner", "create_key"],
                         condition=~models.Q(create_key=""), name="agent_conv_owner_key")]


class AgentWorkTask(models.Model):
    STATES = [(state, state) for state in ("queued", "running", "waiting_input", "waiting_confirmation",
              "stopping", "completed", "failed", "cancelled", "terminated")]
    id = models.UUIDField(primary_key=True, default=identifier, editable=False)
    owner = models.ForeignKey("portal.User", on_delete=models.PROTECT)
    department_code = models.CharField(max_length=20)
    conversation = models.ForeignKey(AgentConversation, on_delete=models.PROTECT)
    project = models.ForeignKey(AgentProject, null=True, blank=True, on_delete=models.PROTECT)
    goal = models.TextField()
    public_summary = models.CharField(max_length=300, blank=True)
    state = models.CharField(max_length=24, choices=STATES, default="queued")
    current_requirement_version = models.PositiveIntegerField(default=0)
    result_references = models.JSONField(default=list)
    stop_reason = models.CharField(max_length=200, blank=True)
    predecessor = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class AgentMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=identifier, editable=False)
    conversation = models.ForeignKey(AgentConversation, on_delete=models.PROTECT)
    work = models.ForeignKey(AgentWorkTask, null=True, blank=True, on_delete=models.PROTECT)
    role = models.CharField(max_length=12, choices=[("user", "user"), ("assistant", "assistant")])
    content = models.TextField()
    attachment_references = models.JSONField(default=list)
    client_request_id = models.CharField(max_length=100, blank=True)
    execution_state = models.CharField(max_length=30, default="pending")
    execution_reason = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["conversation", "client_request_id"],
                         condition=~models.Q(client_request_id=""), name="agent_msg_conv_key")]


class AgentRequirement(models.Model):
    id = models.UUIDField(primary_key=True, default=identifier, editable=False)
    work = models.ForeignKey(AgentWorkTask, on_delete=models.PROTECT)
    version = models.PositiveIntegerField()
    user_message = models.ForeignKey(AgentMessage, on_delete=models.PROTECT)
    content = models.TextField()
    source_versions = models.JSONField(default=list)
    source_hash = models.CharField(max_length=64, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["work", "version"], name="agent_requirement_version")]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            old = type(self).objects.get(pk=self.pk)
            if any(getattr(old, field) != getattr(self, field) for field in
                   ("work_id", "version", "user_message_id", "content", "source_versions", "source_hash")):
                raise ValidationError("要求版本不可修改。")
        return super().save(*args, **kwargs)


class AgentRun(models.Model):
    id = models.UUIDField(primary_key=True, default=identifier, editable=False)
    root_run_id = models.UUIDField(db_index=True)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT)
    conversation = models.ForeignKey(AgentConversation, on_delete=models.PROTECT)
    work = models.ForeignKey(AgentWorkTask, null=True, blank=True, on_delete=models.PROTECT)
    requirement = models.ForeignKey(AgentRequirement, null=True, blank=True, on_delete=models.PROTECT)
    native_thread_id = models.CharField(max_length=160, blank=True)
    native_run_id = models.CharField(max_length=160, blank=True)
    state = models.CharField(max_length=24, default="pending")
    deadline_at = models.DateTimeField(null=True, blank=True)
    policy = models.JSONField(default=dict)
    action_count = models.PositiveIntegerField(default=0)
    model_count = models.PositiveIntegerField(default=0)
    tool_count = models.PositiveIntegerField(default=0)
    launch_count = models.PositiveIntegerField(default=0)
    next_event_seq = models.PositiveBigIntegerField(default=1)
    stop_reason = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class AgentRootAction(models.Model):
    root = models.ForeignKey(AgentRun, on_delete=models.PROTECT)
    action_key = models.CharField(max_length=160)
    kind = models.CharField(max_length=12, choices=[("model", "model"), ("tool", "tool"),
                                                ("launch", "launch")])
    status = models.CharField(max_length=16, default="reserved")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["root", "action_key"], name="agent_root_action_key")]


class AgentEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=identifier, editable=False)
    owner = models.ForeignKey("portal.User", on_delete=models.PROTECT)
    root = models.ForeignKey(AgentRun, on_delete=models.PROTECT)
    work = models.ForeignKey(AgentWorkTask, null=True, blank=True, on_delete=models.PROTECT)
    run = models.ForeignKey(AgentRun, related_name="events", on_delete=models.PROTECT)
    seq = models.PositiveBigIntegerField()
    event_key = models.CharField(max_length=160)
    type = models.CharField(max_length=50)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["root", "seq"], name="agent_event_root_seq"),
                       models.UniqueConstraint(fields=["root", "event_key"], name="agent_event_root_key")]
        ordering = ["seq"]


class AgentBusinessReference(models.Model):
    id = models.UUIDField(primary_key=True, default=identifier, editable=False)
    work = models.ForeignKey(AgentWorkTask, on_delete=models.PROTECT)
    root = models.ForeignKey(AgentRun, on_delete=models.PROTECT)
    requirement = models.ForeignKey(AgentRequirement, on_delete=models.PROTECT)
    domain_type = models.CharField(max_length=60)
    object_id = models.CharField(max_length=160)
    revision = models.CharField(max_length=160, blank=True)
    digest = models.CharField(max_length=64, blank=True)
    operation_key = models.CharField(max_length=160)
    public_summary = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["root", "operation_key"], name="agent_business_operation")]


class AgentSkillInstallation(models.Model):
    owner = models.ForeignKey("portal.User", on_delete=models.PROTECT)
    skill_id = models.CharField(max_length=100)
    version = models.CharField(max_length=80)
    digest = models.CharField(max_length=64)
    enabled = models.BooleanField(default=True)
    installed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner", "skill_id"], name="agent_skill_owner_id")]


@transaction.atomic
def append_public_event(root_run_id, run_id, event_key, event_type, payload, work=None):
    root = AgentRun.objects.select_for_update().get(pk=root_run_id, root_run_id=root_run_id, parent__isnull=True)
    existing = AgentEvent.objects.filter(root=root, event_key=event_key).first()
    if existing:
        return existing, False
    run = AgentRun.objects.get(pk=run_id, root_run_id=root_run_id)
    event = AgentEvent.objects.create(owner=root.conversation.owner, root=root, run=run,
        work=work, seq=root.next_event_seq, event_key=event_key, type=event_type, payload=payload)
    AgentRun.objects.filter(pk=root.pk).update(next_event_seq=F("next_event_seq") + 1)
    return event, True
