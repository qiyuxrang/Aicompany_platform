import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("portal", "0007_product_generation_policy"),
    ]

    operations = [
        migrations.CreateModel(
            name="HrJobTask",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=200)),
                ("department", models.CharField(blank=True, max_length=200)),
                ("objective", models.TextField(blank=True)),
                ("responsibilities", models.TextField(blank=True)),
                ("requirements", models.TextField(blank=True)),
                ("state", models.CharField(choices=[("draft", "草稿"), ("generated", "已生成"), ("confirmed", "已确认")], default="draft", max_length=16)),
                ("version", models.PositiveIntegerField(default=1)),
                ("input_version", models.PositiveIntegerField(default=1)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="hr_job_tasks", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-updated_at", "-created_at"]},
        ),
        migrations.CreateModel(
            name="ProbationCase",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("employee_name", models.CharField(max_length=200)),
                ("position", models.CharField(max_length=200)),
                ("materials", models.JSONField(default=list)),
                ("notes", models.TextField(blank=True)),
                ("manager_opinion", models.TextField(blank=True)),
                ("hr_conclusion", models.TextField(blank=True)),
                ("state", models.CharField(choices=[("draft", "草稿"), ("collecting", "材料收集中"), ("manager_pending", "待主管审批"), ("hr_pending", "待HR确认"), ("archived", "已归档")], default="draft", max_length=24)),
                ("version", models.PositiveIntegerField(default=1)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("assigned_manager", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="managed_probation_cases", to=settings.AUTH_USER_MODEL)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="owned_probation_cases", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-updated_at", "-created_at"]},
        ),
        migrations.CreateModel(
            name="HrJobRevision",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("version", models.PositiveIntegerField()),
                ("input_version", models.PositiveIntegerField()),
                ("kind", models.CharField(choices=[("generated", "确定性草稿"), ("manual", "人工修改"), ("confirmed", "HR确认")], max_length=16)),
                ("body", models.TextField()),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("confirmed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="confirmed_hr_job_revisions", to=settings.AUTH_USER_MODEL)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="hr_job_revisions", to=settings.AUTH_USER_MODEL)),
                ("parent", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="children", to="portal.hrjobrevision")),
                ("task", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="revisions", to="portal.hrjobtask")),
            ],
            options={"ordering": ["version"]},
        ),
        migrations.AddField(model_name="hrjobtask", name="current_revision", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="current_for_tasks", to="portal.hrjobrevision")),
        migrations.AddField(model_name="hrjobtask", name="official_revision", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="official_for_tasks", to="portal.hrjobrevision")),
        migrations.CreateModel(
            name="ProbationTransition",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("from_state", models.CharField(choices=[("draft", "草稿"), ("collecting", "材料收集中"), ("manager_pending", "待主管审批"), ("hr_pending", "待HR确认"), ("archived", "已归档")], max_length=24)),
                ("to_state", models.CharField(choices=[("draft", "草稿"), ("collecting", "材料收集中"), ("manager_pending", "待主管审批"), ("hr_pending", "待HR确认"), ("archived", "已归档")], max_length=24)),
                ("action", models.CharField(max_length=32)),
                ("comment", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="probation_transitions", to=settings.AUTH_USER_MODEL)),
                ("case", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="transitions", to="portal.probationcase")),
            ],
            options={"ordering": ["created_at", "id"]},
        ),
        migrations.AddConstraint(model_name="hrjobtask", constraint=models.CheckConstraint(condition=models.Q(("version__gt", 0)), name="hr_job_version_gt0_ck")),
        migrations.AddConstraint(model_name="hrjobtask", constraint=models.CheckConstraint(condition=models.Q(("input_version__gt", 0)), name="hr_job_input_ver_gt0_ck")),
        migrations.AddConstraint(model_name="hrjobrevision", constraint=models.UniqueConstraint(fields=("task", "version"), name="hr_job_revision_ver_uq")),
        migrations.AddConstraint(model_name="hrjobrevision", constraint=models.CheckConstraint(condition=models.Q(("version__gt", 0)), name="hr_job_revision_ver_gt0_ck")),
        migrations.AddConstraint(model_name="hrjobrevision", constraint=models.CheckConstraint(condition=models.Q(("input_version__gt", 0)), name="hr_job_revision_input_gt0_ck")),
        migrations.AddConstraint(model_name="probationcase", constraint=models.CheckConstraint(condition=models.Q(("version__gt", 0)), name="hr_probation_version_gt0_ck")),
    ]
