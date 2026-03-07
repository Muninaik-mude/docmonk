from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("analyzer", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="analysisjob",
            name="context",
            field=models.TextField(blank=True, default=""),
        ),
    ]
