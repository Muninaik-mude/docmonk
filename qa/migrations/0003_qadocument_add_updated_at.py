"""
Add updated_at to qa_documents — safe for databases that already have the column.

The column was added to 0001_initial.py in-place instead of a new migration,
so Railway's existing database has the table but is missing the column.
Using RunSQL with IF NOT EXISTS makes this idempotent for both cases.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("qa", "0002_add_name_to_qasession"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                ALTER TABLE qa_documents
                ADD COLUMN IF NOT EXISTS updated_at
                TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW();
            """,
            reverse_sql="""
                ALTER TABLE qa_documents DROP COLUMN IF EXISTS updated_at;
            """,
        ),
    ]
