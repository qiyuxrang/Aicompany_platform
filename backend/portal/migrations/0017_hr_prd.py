from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('portal', '0016_merge_product_intake_hr_recruitment')]

    operations = [
        migrations.AddField(model_name='recruitmentrequest', name='salary', field=models.TextField(blank=True)),
        migrations.AddField(model_name='recruitmentrequest', name='benefits', field=models.TextField(blank=True)),
        migrations.AddField(model_name='recruitmentrequest', name='social_insurance', field=models.TextField(blank=True)),
        migrations.AddField(model_name='recruitmentrequest', name='original_text', field=models.TextField(blank=True)),
        migrations.AddField(model_name='recruitmentrequest', name='intake_source',
                            field=models.CharField(default='structured', max_length=16)),
        migrations.AddField(model_name='jdversion', name='requirements', field=models.JSONField(default=dict)),
        migrations.CreateModel(
            name='RecruitmentMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(max_length=16, choices=[('user', '用户'), ('assistant', '助手')])),
                ('content', models.TextField()),
                ('input_version', models.PositiveIntegerField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('request', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                    related_name='messages', to='portal.recruitmentrequest')),
                ('jd_version', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='messages', to='portal.jdversion')),
            ],
            options={'ordering': ['created_at', 'id']},
        ),
    ]
