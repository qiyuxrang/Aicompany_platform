"""Join independent product extraction and HR recruitment histories.

Do not rename or rewrite either 0012: either migration may already be deployed.
Both branches must be present before subsequent schema changes are applied.
"""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("portal", "0012_documentrevision_extraction"),
        ("portal", "0015_hr_screening_batches"),
    ]
    operations = []
