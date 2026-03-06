"""
Stub migration — no operations.

The `name` field on QASession was originally added in this migration and was
later merged into 0001_initial.py. This file is kept as an empty stub so that
databases which already applied the original 0002 have a consistent migration
history. Fresh databases skip it with zero side effects.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("qa", "0001_initial"),
    ]

    operations = []
