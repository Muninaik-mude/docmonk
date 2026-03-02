---
description: Full feature development workflow — explore, implement, review, test
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Task
---

# Feature: $ARGUMENTS

Execute this complete workflow for the requested feature.

## Step 1 — Explore (parallel)
Use the **explorer** subagent to scan the codebase and identify:
- Related models, serializers, views, services
- Existing patterns to follow
- Where new files should be created

While explorer runs, also read the relevant skill files for this feature type.

## Step 2 — Plan
Based on explorer output, create a concise implementation plan:
- Files to create (with purpose)
- Files to modify (with what changes)
- New migration needed? Yes/No

Show the plan. Then implement without waiting for approval unless the plan
reveals something unexpected or risky.

## Step 3 — Implement
Follow patterns from explorer output and relevant skills.
Strictly follow all rules in CLAUDE.md — service layer, serializers per action,
scoped querysets, proper permissions.

If a migration is needed, create it with a descriptive name, then run the
**migration-auditor** subagent immediately.

## Step 4 — Review (parallel with test prep)
Run the **django-reviewer** subagent on all changed files.
Fix every FAIL item before continuing. Minor suggestions are optional.

## Step 5 — Test
Run the **test-runner** subagent. It writes and runs tests.
Do not proceed until tests are green.

## Step 6 — Done
Report:
- What was built (files created/modified)
- Test coverage summary
- Any follow-up notes or known limitations