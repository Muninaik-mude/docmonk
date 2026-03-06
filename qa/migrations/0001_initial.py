import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="QADocument",
            fields=[
                ("id",                models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("document_id",       models.CharField(max_length=500, unique=True, db_index=True)),
                ("s3_download_url",   models.URLField(max_length=2000)),
                ("document_filename", models.CharField(max_length=500, default="document")),
                ("file_type",         models.CharField(max_length=20, default="")),
                ("full_text",         models.TextField()),
                ("char_count",        models.IntegerField(default=0)),
                ("char_page_map",     models.JSONField(default=list)),
                ("created_at",        models.DateTimeField(auto_now_add=True)),
                ("updated_at",        models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "qa_documents",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="QASession",
            fields=[
                ("id",         models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("user_id",    models.CharField(max_length=500, db_index=True)),
                ("name",       models.CharField(max_length=300, default="", blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "qa_sessions",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["user_id", "created_at"], name="qa_sessions_user_id_00b235_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="QAMessage",
            fields=[
                ("id",                   models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("question_message_id",  models.UUIDField(default=uuid.uuid4, editable=False)),
                ("question",             models.TextField()),
                ("answers_per_document", models.JSONField(default=list)),
                ("created_at",           models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at",           models.DateTimeField(auto_now=True)),
                ("session",              models.ForeignKey(
                    to="qa.QASession",
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="messages",
                )),
            ],
            options={
                "db_table": "qa_messages",
                "ordering": ["created_at"],
            },
        ),
        migrations.CreateModel(
            name="QASessionDocument",
            fields=[
                ("id",       models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("position", models.IntegerField(default=0)),
                ("document", models.ForeignKey(
                    to="qa.QADocument",
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="session_links",
                )),
                ("session",  models.ForeignKey(
                    to="qa.QASession",
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="session_documents",
                )),
            ],
            options={
                "db_table": "qa_session_documents",
                "ordering": ["position"],
                "indexes": [
                    models.Index(fields=["session", "position"], name="qa_session__session_72b395_idx"),
                ],
                "unique_together": {("session", "document")},
            },
        ),
    ]
