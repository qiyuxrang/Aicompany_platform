import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("portal", "0008_hr_workflows"),
    ]

    operations = [
        migrations.AddField(
            model_name="probationcase",
            name="assistant_mode",
            field=models.CharField(default="manual", max_length=16),
        ),
        migrations.AddField(
            model_name="probationcase",
            name="assistant_reason",
            field=models.CharField(default="model_not_authorized", max_length=64),
        ),
        migrations.CreateModel(
            name="ProbationRevision",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("before", models.JSONField()),
                ("after", models.JSONField()),
                ("changed_fields", models.JSONField()),
                ("case_version", models.PositiveIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="probation_revisions", to=settings.AUTH_USER_MODEL)),
                ("case", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="revisions", to="portal.probationcase")),
            ],
            options={"ordering": ["created_at", "id"]},
        ),
        migrations.AddConstraint(
            model_name="probationrevision",
            constraint=models.CheckConstraint(condition=models.Q(("case_version__gt", 0)), name="hr_probation_revision_ver_gt0_ck"),
        ),
    ]
