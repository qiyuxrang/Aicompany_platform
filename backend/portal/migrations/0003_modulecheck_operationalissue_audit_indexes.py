import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0002_integrationticket_grant_version_user_grant_version"),
    ]

    operations = [
        migrations.CreateModel(
            name="ModuleCheck",
            fields=[
                (
                    "module",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="latest_check",
                        serialize=False,
                        to="portal.module",
                    ),
                ),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("reachable", "可访问"),
                            ("unavailable", "不可访问"),
                            ("not_configured", "未配置"),
                            ("disabled", "已停用"),
                            ("error", "检查异常"),
                        ],
                        max_length=20,
                    ),
                ),
                ("checked_at", models.DateTimeField()),
                ("next_check_at", models.DateTimeField()),
                ("duration_ms", models.PositiveIntegerField(blank=True, null=True)),
                ("message", models.CharField(max_length=200)),
                ("config_digest", models.CharField(max_length=64)),
            ],
        ),
        migrations.CreateModel(
            name="OperationalIssue",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "severity",
                    models.CharField(
                        choices=[("warning", "警告"), ("critical", "严重")],
                        max_length=20,
                    ),
                ),
                ("title", models.CharField(max_length=120)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("open", "待处理"),
                            ("investigating", "处理中"),
                            ("closed", "已人工关闭"),
                            ("recovered", "已恢复"),
                        ],
                        default="open",
                        max_length=20,
                    ),
                ),
                ("first_seen", models.DateTimeField()),
                ("last_seen", models.DateTimeField()),
                ("occurrences", models.PositiveIntegerField(default=1)),
                ("evidence", models.CharField(max_length=200)),
                ("checked_at", models.DateTimeField()),
                ("notes", models.JSONField(default=list)),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("recovered_at", models.DateTimeField(blank=True, null=True)),
                (
                    "module",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="operational_issue",
                        to="portal.module",
                    ),
                ),
            ],
            options={
                "ordering": ["-last_seen", "-id"],
                "indexes": [
                    models.Index(fields=["status", "last_seen"], name="portal_issue_status_time"),
                    models.Index(fields=["severity", "last_seen"], name="portal_issue_sev_time"),
                ],
            },
        ),
        migrations.AddIndex(
            model_name="auditevent",
            index=models.Index(fields=["action", "result", "created_at"], name="portal_audit_act_res_time"),
        ),
        migrations.AddIndex(
            model_name="auditevent",
            index=models.Index(fields=["actor", "created_at"], name="portal_audit_actor_time"),
        ),
    ]
