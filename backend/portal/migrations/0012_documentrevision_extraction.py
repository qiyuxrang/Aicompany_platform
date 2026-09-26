from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("portal", "0011_product_version_lineage")]
    operations = [migrations.AlterField(model_name="documentrevision", name="kind", field=models.CharField(choices=[("input", "输入"), ("extraction", "资料解析快照"), ("blueprint", "蓝图"), ("chapter", "章节"), ("review", "审查"), ("report", "报告内容")], max_length=16))]
