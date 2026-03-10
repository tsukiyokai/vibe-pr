# Phase 1.5: Self-check

Before push: review your own changes. Fix issues automatically, report unfixable ones.

## 1.5.1 Call vibe-review skill

Run `/vibe-review` on local diff. vibe-review has:
- 1124-line coding standard (naming, formatting, safety, HCCL project rules)
- 12 HCCL high-frequency defect patterns
- 5-step review methodology

Steps:
1. Generate diff with `git diff`
2. Call vibe-review skill on the diff
3. Fix each finding
4. Re-diff to confirm fixes

## 1.5.2 Checks not covered by vibe-review

Copyright header: all new .h/.cpp/.cc/.cxx files must have CANN Open Software License Agreement Version 2.0 header (template in workflow.md).

Commit message format: must follow `<type>(<scope>): <subject>`.
Valid types: feat / fix / docs / style / refactor / perf / test / chore.

## 1.5.3 Pass criteria

- All "critical" findings from vibe-review: fixed
- All "normal" findings: fixed (no excuse for leaving issues in your own code)
- "Suggestion" findings: fix at your discretion
- Copyright header and commit message format: correct
- Only push and create PR after passing all checks
