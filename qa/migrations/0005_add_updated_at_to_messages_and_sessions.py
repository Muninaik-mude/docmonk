"""
Add missing updated_at columns to qa_messages and qa_sessions.

All three qa tables (documents, messages, sessions) had updated_at added
to 0001_initial.py in-place after Railway had already created the tables.
qa_documents was fixed by removing the field from the model (0004).
qa_messages and qa_sessions cannot have the field removed — it is used
in API responses and service-layer save() calls — so the columns must be
added to the existing tables.

SeparateDatabaseAndState is used because Django's migration state already
has these fields (from 0001_initial.py), so only the DB side needs work.
ADD COLUMN IF NOT EXISTS is safe for both Railway (column missing) and
fresh deployments (column already created by 0001).
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("qa", "0004_remove_qadocument_updated_at"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[],  # fields already in model state via 0001_initial.py
            database_operations=[
                migrations.RunSQL(
                    sql="""
                        ALTER TABLE qa_messages
                        ADD COLUMN IF NOT EXISTS updated_at
                        TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW();
                    """,
                    reverse_sql="ALTER TABLE qa_messages DROP COLUMN IF EXISTS updated_at;",
                ),
                migrations.RunSQL(
                    sql="""
                        ALTER TABLE qa_sessions
                        ADD COLUMN IF NOT EXISTS updated_at
                        TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW();
                    """,
                    reverse_sql="ALTER TABLE qa_sessions DROP COLUMN IF EXISTS updated_at;",
                ),
            ],
        ),
    ]
