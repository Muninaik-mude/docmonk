from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("analyzer", "0003_analysisjob_policy_fields"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="analysisjob",
            name="policy_type",
        ),
        migrations.RemoveField(
            model_name="analysisjob",
            name="policy_text",
        ),
        migrations.RemoveField(
            model_name="analysisjob",
            name="policy_result",
        ),
    ]
