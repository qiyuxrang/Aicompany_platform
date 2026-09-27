import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
import uuid


class Migration(migrations.Migration):
    dependencies = [
        ('portal', '0020_product_source_purpose'),
    ]

    operations = [
        migrations.CreateModel(
            name='BusinessLedgerGrant',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('department', models.CharField(choices=[('engineering', '工程'), ('finance', '财务'), ('presales', '售前')], max_length=16)),
                ('can_edit', models.BooleanField(default=False)),
                ('can_submit', models.BooleanField(default=False)),
                ('can_publish', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='business_ledger_grants', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['department', 'user_id'],
                'indexes': [models.Index(fields=['user', 'department'], name='business_grant_scope_idx')],
                'constraints': [
                    models.UniqueConstraint(fields=('user', 'department'), name='business_grant_user_department_uniq'),
                    models.CheckConstraint(condition=models.Q(('can_edit', True), ('can_submit', True), ('can_publish', True), _connector='OR'), name='business_grant_has_capability'),
                ],
            },
        ),
        migrations.CreateModel(
            name='BusinessLedgerWorkbook',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('department', models.CharField(choices=[('engineering', '工程'), ('finance', '财务'), ('presales', '售前')], max_length=16, unique=True)),
                ('state', models.CharField(choices=[('draft', '草稿'), ('submitted', '已提交'), ('published', '已发布')], default='draft', max_length=16)),
                ('revision', models.PositiveBigIntegerField(default=0)),
                ('source_name', models.CharField(default='手工录入', max_length=200)),
                ('as_of', models.DateField(blank=True, null=True)),
                ('records', models.JSONField(default=list)),
                ('last_return_reason', models.CharField(blank=True, max_length=500)),
                ('submitted_at', models.DateTimeField(blank=True, null=True)),
                ('published_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='created_business_ledgers', to=settings.AUTH_USER_MODEL)),
                ('published_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='published_business_ledgers', to=settings.AUTH_USER_MODEL)),
                ('submitted_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='submitted_business_ledgers', to=settings.AUTH_USER_MODEL)),
                ('updated_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='updated_business_ledgers', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['department']},
        ),
        migrations.CreateModel(
            name='BusinessLedgerRevision',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('revision', models.PositiveBigIntegerField()),
                ('state', models.CharField(choices=[('draft', '草稿'), ('submitted', '已提交'), ('published', '已发布')], max_length=16)),
                ('source_name', models.CharField(max_length=200)),
                ('as_of', models.DateField(blank=True, null=True)),
                ('records', models.JSONField(default=list)),
                ('checksum', models.CharField(max_length=64)),
                ('action', models.CharField(max_length=32)),
                ('return_reason', models.CharField(blank=True, max_length=500)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('actor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='business_ledger_versions', to=settings.AUTH_USER_MODEL)),
                ('workbook', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='versions', to='portal.businessledgerworkbook')),
            ],
            options={
                'ordering': ['-created_at', '-revision'],
                'indexes': [models.Index(fields=['state', '-created_at'], name='business_revision_state_time'), models.Index(fields=['workbook', '-revision'], name='business_revision_book_number')],
                'constraints': [models.UniqueConstraint(fields=('workbook', 'revision'), name='business_revision_workbook_number_uniq')],
            },
        ),
    ]
