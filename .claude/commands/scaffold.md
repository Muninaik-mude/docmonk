---
description: Scaffold a complete new Django app — models, serializers, services, views, urls, admin, factories, tests
---

# Scaffold New App: $ARGUMENTS

Create a complete, production-ready Django app named `$ARGUMENTS` under `apps/$ARGUMENTS/`.

## Step 1 — Read existing patterns
Use the **explorer** subagent to check one existing app (e.g. `apps/users/`) and
identify the exact conventions this project follows for structure and naming.

## Step 2 — Create the app

```bash
python manage.py startapp $ARGUMENTS apps/$ARGUMENTS
```

Then create the full file structure:

```
apps/$ARGUMENTS/
├── __init__.py
├── apps.py              ← AppConfig with name = 'apps.$ARGUMENTS'
├── models.py            ← BaseModel subclass with example fields + __str__
├── serializers.py       ← List + Detail + Create serializers
├── services.py          ← Empty service class with docstring
├── views.py             ← ModelViewSet with get_queryset + get_serializer_class
├── urls.py              ← DefaultRouter registration
├── admin.py             ← ModelAdmin registration
├── signals.py           ← Empty, ready for post_save hooks
├── exceptions.py        ← App-specific exception classes
├── filters.py           ← FilterSet for common filter fields
└── tests/
    ├── __init__.py
    ├── factories.py     ← DjangoModelFactory with Faker
    ├── test_models.py   ← Model __str__, fields, constraints
    ├── test_services.py ← Service happy + failure cases
    └── test_views.py    ← CRUD + permission boundary tests
```

## Step 3 — Register the app

Add `'apps.$ARGUMENTS'` to `LOCAL_APPS` in `config/settings/base.py`.
Include URLs in `config/urls.py` under `/api/v1/$ARGUMENTS/`.

## Step 4 — Generate initial migration

```bash
python manage.py makemigrations $ARGUMENTS --name initial
```

Run **migration-auditor** on the generated migration.

## Step 5 — Verify

```bash
python manage.py check
pytest apps/$ARGUMENTS/tests/ -v
```

Fix any errors before continuing.

## Step 6 — Review

Run the **django-reviewer** subagent on all files created in `apps/$ARGUMENTS/`.
Fix every finding before continuing. Do not skip this step.

## Step 7 — Report

List all created files with a one-line purpose for each.
State: "App `$ARGUMENTS` is scaffolded and ready for implementation."