import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('portal', '0018_product_knowledge'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(
        name='BusinessLedgerSnapshot',
        fields=[
            ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ('department', models.CharField(max_length=16)),
            ('source_name', models.CharField(max_length=200)),
            ('as_of', models.DateField()),
            ('records', models.JSONField(default=list)),
            ('checksum', models.CharField(max_length=64)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ledger_snapshots', to=settings.AUTH_USER_MODEL)),
        ],
        options={'ordering': ['-created_at', '-id'], 'indexes': [models.Index(fields=['owner', 'department', '-created_at'], name='business_snapshot_scope_idx')]},
    )]
