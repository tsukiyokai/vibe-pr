# Phase 4: Trigger CI and Handle Failures

## Trigger CI

CI does not auto-trigger. Post `compile` in PR comments:

```bash
python3 -c "
from gitcode_api import get_token, post_comment
post_comment('<repo>', get_token(), <pr_number>, 'compile')
"
```

CI runs ~20-30 minutes. On pass: label `ci-pipeline-passed` added automatically.
Each new push removes `ci-pipeline-passed` -- must re-trigger CI.

Check CI status:
```bash
python3 ~/.claude/skills/vibe-pr/scripts/pr_status.py --repo cann/hcomm --pr <number>
```

## CI Failure Handling

On failure: attempt self-diagnosis and fix before escalating to user.

### 4.1 Get Failure Info

Use ci_log_fetcher.py to extract CI results:
```bash
python3 ~/.claude/skills/vibe-pr/scripts/ci_log_fetcher.py --repo cann/hcomm --pr <number> --source auto
```

If ci_log_fetcher returns insufficient info, ask user to paste log snippet from CodeArts web UI.

For complex failures, dispatch the ci-analyzer agent:
```
Agent dispatch: ci-analyzer
Input: {"repo": "<repo>", "pr": <number>, "repo_root": "<path>"}
```

### 4.2 Fix by Failure Type

Compile errors (Compile_Ascend_X86 / Compile_Ascend_ARM):
- Extract filename, line number, error description
- Locate code, fix compile issue
- Common causes: missing include, type mismatch, undefined symbol

Static check (codecheck):
- Fix per CANN C++ coding standard (see references/codecheck-rules.md)
- Mostly mechanical: naming, formatting, safe function replacements
- Can re-run self-check (Phase 1.5) to cover

Test failures (UT/ST/Smoke):
- Distinguish regression (our change broke it) vs flaky test
- Regression: read test code, understand expected behavior, fix our code
- Suspected flaky: check if same test fails on master

PR content check (Check_Pr):
- Usually commit message format, copyright header, file naming
- Go back to Phase 1.5 checks

### 4.3 After Fix

1. Fix code
2. Re-run self-check (Phase 1.5)
3. Push update
4. Re-trigger CI (post `compile`)
5. Wait for CI result

Two consecutive failures without resolution: escalate to user with failure log and analysis. Do not loop forever.

Update context: record CI failure cause and fix approach.
