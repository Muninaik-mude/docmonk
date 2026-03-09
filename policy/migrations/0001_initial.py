import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="PolicyAnalysisJob",
            fields=[
                ("job_id",            models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("status",            models.CharField(choices=[("pending", "Pending"), ("in_progress", "In Progress"), ("completed", "Completed"), ("failed", "Failed")], db_index=True, default="pending", max_length=20)),
                ("created_at",        models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at",        models.DateTimeField(auto_now=True)),
                ("document_filename", models.CharField(blank=True, default="", max_length=500)),
                ("full_text",         models.TextField(blank=True, default="")),
                ("policy_type",       models.CharField(blank=True, default="", max_length=500)),
                ("policy_text",       models.TextField(blank=True, default="")),
                ("agreement_type",    models.CharField(blank=True, default="", max_length=500)),
                ("agreement_details", models.JSONField(blank=True, null=True)),
                ("parties",           models.JSONField(blank=True, null=True)),
                ("policy_result",     models.JSONField(blank=True, null=True)),
                ("error_message",     models.TextField(blank=True, default="")),
            ],
            options={
                "db_table": "policy_analysis_jobs",
                "ordering": ["-created_at"],
            },
        ),
    ]
