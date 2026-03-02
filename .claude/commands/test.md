---
description: Write and run tests for a specific app or file
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# Test: $ARGUMENTS

Use the **test-runner** subagent to write comprehensive tests for: $ARGUMENTS

Coverage must include:
- Happy path (correct input + correct user = expected response)
- Validation failures (bad input = correct error response)
- Permission boundaries:
  - Unauthenticated request → 401
  - Authenticated but wrong user → 403 or 404
  - Owner → 200
  - Admin/staff → 200
- Edge cases relevant to the domain

Run tests after writing:
```bash
pytest $ARGUMENTS -v --tb=short
```

Do not finish until all tests are green.
Report the test file path and a summary of what's covered.