from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('portal', '0028_tender_project_groups_and_user_state')]
    operations = [migrations.AddField(model_name='tenderopportunity', name='extraction_evidence',
                                     field=models.JSONField(blank=True, default=dict, verbose_name='公开字段提取证据'))]
