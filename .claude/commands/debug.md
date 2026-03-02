---
description: Diagnose and fix a bug — explore, hypothesize, fix, verify
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# Debug: $ARGUMENTS

## Step 1 — Explore
Use the **explorer** subagent to find all code related to this bug.
Also search for the error message or symptom in the codebase:
```
Grep for: $ARGUMENTS
```

## Step 2 — Diagnose
Read the relevant files. Identify:
- Root cause (not symptoms)
- Why it's happening
- What the correct behaviour should be

## Step 3 — Fix
Implement the fix. Follow all CLAUDE.md rules — don't introduce new violations while fixing.

If a migration is needed, run the **migration-auditor** subagent.

## Step 4 — Verify
Run existing tests to check for regressions:
```bash
pytest apps/ -x --tb=short -q
```

If a test was missing that would have caught this bug — write it now using **test-runner**.

## Step 5 — Report
- Root cause (one paragraph)
- What was changed
- Test that now covers this case