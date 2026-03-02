---
description: Verify changes are ready and prepare a pull request description
allowed-tools: Bash(git diff:*), Bash(git status:*), Bash(git log:*), Bash(pytest:*), Read, Glob
---

# Pull Request Preparation

## Current Status
!`git status`

## Branch vs Main
!`git log main..HEAD --oneline`

## Changes
!`git diff main --stat`

---

## Step 1 — Run tests
```bash
pytest apps/ -q --tb=short
```
If tests fail, fix them before continuing.

## Step 2 — Final review
Run **django-reviewer** on the full diff vs main.
Fix any FAIL items.

## Step 3 — Write PR description

Using the git log and diff above, write:

**Title:** [concise, imperative — e.g. "Add order cancellation endpoint"]

**Summary:**
What this PR does and why (2-3 sentences).

**Changes:**
- List of meaningful changes (not file-by-file, but feature-level)

**Testing:**
What tests were added/modified and what they cover.

**Notes:**
Any migration, env var changes, or deployment notes the reviewer needs to know.