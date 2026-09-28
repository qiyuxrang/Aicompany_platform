from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('portal', '0025_merge_engineering_tender')]

    operations = [
        migrations.AddField(model_name='tenderopportunity', name='industry_code',
                            field=models.CharField('行业代码', max_length=24, blank=True, db_index=True)),
        migrations.AddField(model_name='tenderopportunity', name='digital_tags',
                            field=models.JSONField('数字建设标签', default=list, blank=True)),
        migrations.AddField(model_name='tenderopportunity', name='classification_status',
                            field=models.CharField('相关性', max_length=12, default='review', db_index=True)),
        migrations.AddField(model_name='tenderopportunity', name='notice_category',
                            field=models.CharField('公告分组', max_length=16, default='procurement', db_index=True)),
        migrations.AddField(model_name='tenderopportunity', name='classification_evidence',
                            field=models.JSONField('分类证据', default=dict, blank=True)),
        migrations.AddField(model_name='tenderopportunity', name='classification_version',
                            field=models.CharField('分类规则版本', max_length=40, blank=True)),
        migrations.AddField(model_name='tenderopportunity', name='classification_notice_version',
                            field=models.ForeignKey(blank=True, null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                related_name='classified_opportunities', to='portal.tendernoticeversion')),
        migrations.AddIndex(model_name='tenderopportunity', index=models.Index(
            fields=['classification_status', 'notice_category', 'industry_code'],
            name='tender_opp_board_filter')),
    ]
