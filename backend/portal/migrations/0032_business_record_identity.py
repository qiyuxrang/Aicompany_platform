from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('portal', '0031_hr_long_term_retention')]

    operations = [
        migrations.AddField(
            model_name='businessledgerworkbook', name='record_meta', field=models.JSONField(default=dict),
        ),
        migrations.AddField(
            model_name='businessledgerrevision', name='record_meta', field=models.JSONField(default=dict),
        ),
        migrations.AddField(
            model_name='businessledgerrevision', name='request_fingerprint',
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
