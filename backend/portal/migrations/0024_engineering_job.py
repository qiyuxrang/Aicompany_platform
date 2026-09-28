import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("portal", "0023_hr_model_selection"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="EngineeringJob",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("region", models.CharField(default="陕西", max_length=80)),
                ("inputs", models.JSONField(default=list)),
                ("inspection", models.JSONField(blank=True, default=dict)),
                ("result", models.JSONField(blank=True, default=dict)),
                ("result_path", models.CharField(blank=True, max_length=500)),
                ("result_filename", models.CharField(blank=True, max_length=200)),
                ("result_sha256", models.CharField(blank=True, max_length=64)),
                ("result_size", models.PositiveBigIntegerField(blank=True, null=True)),
                ("status", models.CharField(choices=[("queued", "排队中"), ("running", "处理中"), ("completed", "已完成"), ("failed", "失败"), ("blocked", "阻塞")], db_index=True, default="queued", max_length=20)),
                ("error_code", models.CharField(blank=True, max_length=64)),
                ("error_detail", models.CharField(blank=True, max_length=300)),
                ("attempt_count", models.PositiveIntegerField(default=0)),
                ("fence", models.PositiveIntegerField(default=0)),
                ("lease_until", models.DateTimeField(blank=True, null=True)),
                ("next_retry_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="engineering_jobs", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [models.Index(fields=["status", "next_retry_at", "created_at"], name="portal_eng_status_retry")],
            },
        ),
    ]
