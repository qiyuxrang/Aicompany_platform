from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0010_product_draft_families"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="documentrevision",
            name="parent_sha256",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="documentrevision",
            name="change_reason",
            field=models.CharField(default="legacy_unspecified", max_length=80),
        ),
        migrations.AddField(
            model_name="documentsource",
            name="uploaded_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="uploaded_document_sources", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="documentsource",
            name="author_verification",
            field=models.CharField(default="unverified", max_length=24),
        ),
    ]