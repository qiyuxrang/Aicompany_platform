from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("portal", "0006_documentapproval_authorization")]

    operations = [
        migrations.AddField(model_name="documentartifact", name="generation_hash", field=models.CharField(blank=True, default="", max_length=64)),
        migrations.AddConstraint(model_name="documentartifact", constraint=models.UniqueConstraint(fields=("task", "generation_hash"), condition=~models.Q(generation_hash=""), name="product_artifact_generation_uq")),
        migrations.CreateModel(name="DocumentReviewPolicy", fields=[("id", models.PositiveSmallIntegerField(default=1, primary_key=True, serialize=False)), ("fingerprint", models.CharField(max_length=64)), ("version", models.PositiveBigIntegerField(default=1))]),
    ]
