from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("portal", "0005_product_p1")]

    operations = [migrations.AddField(model_name="documentapproval", name="authorization", field=models.JSONField(default=dict))]
