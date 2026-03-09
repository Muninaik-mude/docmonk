from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("analyzer", "0002_analysisjob_context"),
    ]

    operations = [
        migrations.AddField(
            model_name="analysisjob",
            name="policy_type",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="analysisjob",
            name="policy_text",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="analysisjob",
            name="policy_result",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
