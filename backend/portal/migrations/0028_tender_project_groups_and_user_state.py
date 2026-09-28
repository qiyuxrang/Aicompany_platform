from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('portal', '0027_tender_hourly_schedule'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField(model_name='tenderopportunity', name='project_group_key',
                            field=models.CharField(blank=True, db_index=True, max_length=255, verbose_name='跨来源项目组')),
        migrations.CreateModel(name='TenderOpportunityUserState', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('project_group_key', models.CharField(max_length=255)),
            ('is_read', models.BooleanField(default=False)),
            ('is_favorite', models.BooleanField(default=False)),
            ('is_irrelevant', models.BooleanField(default=False)),
            ('updated_at', models.DateTimeField(auto_now=True)),
            ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ], options={'constraints': [models.UniqueConstraint(fields=('user', 'project_group_key'), name='tender_user_project_state')]}),
    ]
