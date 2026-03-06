from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("qa", "0005_add_updated_at_to_messages_and_sessions"),
    ]

    operations = [
        migrations.AlterField(
            model_name="qadocument",
            name="s3_download_url",
            field=models.URLField(max_length=2000, null=True, blank=True),
        ),
    ]
