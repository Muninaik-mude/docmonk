"""
Remove updated_at from QADocument — model and DB.

QADocument is a write-once extraction cache. S3 keys are immutable, so the
extracted text never changes after creation. updated_at has no meaning here.

The column is missing from Railway's existing DB (it was added to 0001_initial
in-place after the table was already created). Using SeparateDatabaseAndState
so Django's migration state stays consistent while the DB side uses
DROP COLUMN IF EXISTS — safe regardless of whether the column currently exists.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("qa", "0003_qadocument_add_updated_at"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name="qadocument",
                    name="updated_at",
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql="ALTER TABLE qa_documents DROP COLUMN IF EXISTS updated_at;",
                    reverse_sql="ALTER TABLE qa_documents ADD COLUMN updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW();",
                ),
            ],
        ),
    ]
