import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("portal", "0017_hr_prd")]
    operations = [migrations.CreateModel(
        name="ProductKnowledgeConversation",
        fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ("title", models.CharField(default="新对话", max_length=100)),
            ("scope", models.JSONField(default=dict)),
            ("turns", models.JSONField(default=list)),
            ("version", models.PositiveIntegerField(default=0)),
            ("pending_id", models.UUIDField(blank=True, null=True)),
            ("pending_until", models.DateTimeField(blank=True, null=True)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("updated_at", models.DateTimeField(auto_now=True)),
            ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ], options={"ordering": ["-updated_at", "id"]},
    )]
