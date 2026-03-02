---
description: Create a Django migration and audit it for safety before applying
allowed-tools: Bash, Read, Glob
---

# Migrate: $ARGUMENTS

## Step 1 — Create migration
```bash
python manage.py makemigrations --name $ARGUMENTS
```

## Step 2 — Audit
Run the **migration-auditor** subagent on the newly created migration file.
If it returns RISKY — fix the migration before continuing.
Do not run `migrate` until auditor returns SAFE.

## Step 3 — Apply (only if SAFE)
```bash
python manage.py migrate
```

## Step 4 — Report
- Migration file name
- What schema changes were made
- Auditor result (SAFE / RISKY + resolution)