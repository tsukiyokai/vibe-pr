# vibe-pr Optimization Design: Sub-agent Hybrid Architecture

Date: 2026-03-10

## Background

vibe-pr is a CANN community PR lifecycle bot covering 7 stages (requirements -> fork+push -> self-check -> create PR -> CLA -> CI -> reviewer -> tracking). Current pain points:

1. CI log fetching requires manual login to CodeArts
2. Review comment fix cycle is manual (edit -> commit -> push -> re-trigger CI)
3. No global view when managing 2-5 concurrent PRs
4. Review comment tracking lacks status visibility, auto-reply, and fix quality

## Architecture

```
+-----------------------------------------------------+
|  SKILL.md (< 200 lines)                            |
|  Core workflow instructions + agent dispatch rules  |
+-----------------------------------------------------+
|                                                     |
|  +--------------+  +---------------+  +-----------+ |
|  | review-      |  | ci-analyzer   |  | pr-       | |
|  | responder    |  | agent         |  | dashboard | |
|  | agent        |  |               |  | agent     | |
|  +------+-------+  +-------+-------+  +-----+-----+ |
|         |                  |               |         |
+---------+------------------+---------------+---------+
|  scripts/ (deterministic API layer)                 |
|  gitcode_api.py | comment_parser.py | pr_status.py  |
|  task_context.py | reviewer_activity.py             |
|  + NEW: ci_log_fetcher.py | review_tracker.py       |
|  + ENHANCED: pr_monitor.py | pr_dashboard.py        |
+---------+------------------+---------------+---------+
|  references/ (on-demand loading)                    |
|  workflow.md | phase-*.md | codecheck-rules.md      |
|  reply-templates.md                                 |
+-----------------------------------------------------+
```

Core division:

- SKILL.md: slim dispatcher, determines current phase, decides which agent to call
- Sub-agents: handle tasks requiring deep understanding (review fixes, CI analysis), each with independent context window
- Scripts: pure API calls and data processing (deterministic, no LLM needed)
- References: detailed workflow docs, coding standards, CI patterns, loaded on demand by agents

## Component Designs

### 1. Review Responder Agent

File: `agents/review-responder.md`

```yaml
name: review-responder
description: >
  Handle code review comments on CANN PRs. Reads related source code,
  applies coding standards, fixes issues, and replies to reviewers.
model: inherit
tools: [Read, Edit, Grep, Glob, Bash]
```

Input (via prompt):
- PR metadata (repo, pr number, branch)
- Review comments to process (comment_parser.py output)
- Project root path

Workflow:
1. For each comment:
   - suggestion -> locate file, read context (+-50 lines), load coding standards, fix code, draft reply, record in review_tracker as "fixed"
   - question -> analyze context, draft suggested reply, record as "needs_user", return to main conversation
2. Self-check each fix (basic syntax verification)
3. Max 10 comments per batch

Output:
```json
{
  "fixed": [{"comment_id": 123, "file": "src/foo.cc", "summary": "...", "reply_draft": "..."}],
  "needs_user": [{"comment_id": 456, "question": "...", "suggested_reply": "..."}],
  "errors": []
}
```

Main conversation handles:
- "fixed" items: show summary, ask user to batch-send replies
- "needs_user" items: show question + suggested reply, let user edit before sending

### 2. CI Analyzer Agent

File: `agents/ci-analyzer.md`

```yaml
name: ci-analyzer
description: >
  Analyze CI failures on CANN PRs. Fetch logs from PR comments
  or CodeArts API, identify root cause, and suggest or apply fixes.
model: inherit
tools: [Read, Edit, Grep, Glob, Bash]
```

Log fetching strategy (layered fallback):
1. CodeArts API (if available) -> ci_log_fetcher.py --source api
2. PR comment parsing (default) -> ci_log_fetcher.py --source comments
3. User-provided (final fallback) -> ask user to paste log snippet

Failure type handling:
- Compile error -> extract file:line:error, read source, fix, mark auto_fixed
- Static check (codecheck) -> extract rule ID, load codecheck-rules.md, fix, mark auto_fixed
- Test failure -> distinguish regression vs flaky; regression: attempt fix; flaky: suggest /retest
- No detailed log -> generate guidance on where to look

Output:
```json
{
  "auto_fixed": [{"task": "compile", "file": "src/foo.cc:42", "fix": "..."}],
  "needs_user": [{"task": "ut_test", "reason": "flaky test, suggest /retest"}],
  "summary": "1 compile error fixed, 1 flaky test needs manual retest"
}
```

### 3. New Script: ci_log_fetcher.py

```bash
python3 ci_log_fetcher.py --repo cann/hcomm --pr 584 \
    --source auto \          # auto | api | comments
    --task all               # compile | codecheck | all
```

Output:
```json
{
  "source": "comments",
  "tasks": [
    {"name": "compile", "status": "fail", "log_url": "...", "log_snippet": "..."}
  ]
}
```

CodeArts API mode: interface reserved with NotImplementedError, to be implemented when API access is confirmed.

### 4. New Script: review_tracker.py

Storage: `~/.claude/skills/vibe-pr/context/{repo}_{pr}_reviews.json`

```json
{
  "repo": "cann/hcomm",
  "pr": 584,
  "last_synced": "2026-03-10T14:00:00Z",
  "comments": [
    {
      "id": 123,
      "author": "zhang_wei",
      "type": "suggestion",
      "file": "src/foo.cc",
      "line": 42,
      "body": "rename this variable...",
      "status": "fixed",
      "fix_summary": "renamed to xxx",
      "reply_sent": true,
      "updated_at": "2026-03-10T14:30:00Z"
    }
  ]
}
```

CLI:
```bash
review_tracker.py --sync                    # sync PR comments to local
review_tracker.py --update 123 --status fixed --summary "..."
review_tracker.py --pending                 # show pending items
review_tracker.py --summary                 # board summary: 5 total | 3 fixed | 1 needs_user | 1 pending
```

### 5. New Script: pr_dashboard.py

```bash
pr_dashboard.py --active      # auto-discover from context/ directory
pr_dashboard.py --repos cann/hcomm cann/hccl
```

Output: aggregated table showing PR, Repo, Title, CLA, CI, Review progress, Next Action. Data sourced from pr_status.py + review_tracker.py + task_context.py. No direct API calls.

### 6. PR Monitor Enhancement

pr_monitor.py transforms from "fixer" to "dispatcher":

Before: detect comment -> directly call `claude -p` to fix
After: detect comment -> sync to review_tracker -> dispatch to sub-agent -> collect results -> update tracker -> report to user

New features:
- `--once` mode: single check without polling
- `--interval N --adaptive`: configurable + smart interval
- Consecutive 2 failures on same comment -> escalate to user
- Changes spanning 3+ files -> require user confirmation

### 7. SKILL.md Progressive Disclosure

529 lines -> ~180 lines. Content migration:

| Current SKILL.md section | Destination |
|---|---|
| Phase 0 detailed steps | references/phase-0-requirements.md |
| Self-check specs (copyright, commit msg) | references/phase-1-selfcheck.md |
| CI trigger + failure handling | references/phase-4-ci.md |
| Reviewer selection principles | references/phase-5-reviewer.md |

Slim SKILL.md structure:
- Quick Start (10 lines): phase decision tree
- Phase Overview (30 lines): one-liner per phase + trigger condition
- Agent Dispatch (30 lines): which agent for which situation
- Autonomy Boundaries (20 lines): unchanged
- Cross-session Context (15 lines): unchanged
- Script Reference (40 lines): one-liner + example per script
- Phase Details (30 lines): key points + @references/ pointers

New reference files:
- references/codecheck-rules.md: static check rules for ci-analyzer
- references/reply-templates.md: review reply templates for review-responder

## File Change Summary

New files:
- agents/review-responder.md
- agents/ci-analyzer.md
- scripts/ci_log_fetcher.py
- scripts/review_tracker.py
- scripts/pr_dashboard.py
- references/phase-0-requirements.md
- references/phase-1-selfcheck.md
- references/phase-4-ci.md
- references/phase-5-reviewer.md
- references/codecheck-rules.md
- references/reply-templates.md
- docs/plans/2026-03-10-vibe-pr-optimization-design.md

Modified files:
- SKILL.md (529 -> ~180 lines)
- scripts/pr_monitor.py (fixer -> dispatcher)

Unchanged files:
- scripts/gitcode_api.py
- scripts/comment_parser.py
- scripts/pr_status.py
- scripts/task_context.py
- scripts/reviewer_activity.py
- scripts/issue_parser.py
- references/workflow.md
