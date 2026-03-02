---
description: Run production-quality review on recent git changes
allowed-tools: Read, Glob, Grep, Bash(git diff:*), Bash(git status:*)
---

# Code Review

## Changed Files
!`git diff --name-only HEAD`

## Full Diff
!`git diff HEAD`

---

Review all changed files above using the **django-reviewer** subagent checklist.

Cover:
- N+1 query risks (missing select_related / prefetch_related)
- Business logic leaking into views (should be in services.py)
- Permission issues (unscoped querysets, weak permission_classes)
- Security issues (write_only passwords, hardcoded values)
- Transaction safety (missing @transaction.atomic on multi-step writes)
- Architecture violations (wrong layer, circular imports)

Output PASS or FAIL with specific file + line references for every finding.