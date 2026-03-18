# vibe-pr Optimization Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade vibe-pr to a sub-agent hybrid architecture with review-responder, ci-analyzer agents, new tracking/dashboard scripts, and progressive disclosure SKILL.md refactor.

**Architecture:** Introduce two sub-agents (review-responder, ci-analyzer) for complex LLM tasks, three new scripts (review_tracker.py, ci_log_fetcher.py, pr_dashboard.py) for deterministic operations, enhance pr_monitor.py as a dispatcher, and slim SKILL.md from 529 to ~180 lines with references.

**Tech Stack:** Python 3 (scripts), Markdown + YAML frontmatter (agents), GitCode REST API, existing gitcode_api.py as API layer.

**Design doc:** `docs/plans/2026-03-10-vibe-pr-optimization-design.md`

---

## Chunk 1: Foundation Scripts

### Task 1: review_tracker.py

**Files:**
- Create: `scripts/review_tracker.py`
- Read: `scripts/comment_parser.py` (reuse classify_comment, parse_pr_comments)
- Read: `scripts/gitcode_api.py` (reuse get_token, post_comment)

This script maintains per-PR review comment status, persisted as JSON in the context directory.

- [ ] **Step 1: Create review_tracker.py with data model and storage**

```python
#!/usr/bin/env python3
"""Review comment status tracker for CANN PRs."""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

CONTEXT_DIR = Path.home() / ".claude" / "skills" / "vibe-pr" / "context"


def _review_path(repo: str, pr: int) -> Path:
    """Return path to review tracker JSON file."""
    safe_name = repo.replace("/", "_")
    return CONTEXT_DIR / f"{safe_name}_{pr}_reviews.json"


def _load(repo: str, pr: int) -> dict:
    """Load existing tracker or return empty structure."""
    path = _review_path(repo, pr)
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {
        "repo": repo,
        "pr": pr,
        "last_synced": None,
        "comments": [],
    }


def _save(repo: str, pr: int, data: dict) -> None:
    """Persist tracker to disk."""
    CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
    path = _review_path(repo, pr)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.rename(path)
```

- [ ] **Step 2: Implement sync command**

Sync fetches PR comments via comment_parser.parse_pr_comments, merges new comments into the tracker, preserving status of existing ones.

```python
def sync(repo: str, pr: int) -> dict:
    """Sync PR comments into tracker. Returns updated tracker."""
    # Add scripts/ to path so we can import comment_parser
    scripts_dir = str(Path(__file__).parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import comment_parser

    parsed = comment_parser.parse_pr_comments(repo, pr, since_commit=False)
    tracker = _load(repo, pr)

    existing_ids = {c["id"] for c in tracker["comments"]}

    # Only track review_suggestion and review_question types
    review_types = {"review_suggestion", "review_question"}

    for c in parsed.get("review_comments", []):
        if c["type"] not in review_types:
            continue
        if c["id"] in existing_ids:
            continue
        tracker["comments"].append({
            "id": c["id"],
            "author": c["author"],
            "type": c["type"].replace("review_", ""),  # "suggestion" or "question"
            "file": c.get("file", ""),
            "line": c.get("line"),
            "body": c["body"],
            "status": "pending",
            "fix_summary": "",
            "reply_sent": False,
            "updated_at": None,
        })

    tracker["last_synced"] = datetime.now().isoformat()
    _save(repo, pr, tracker)
    return tracker
```

- [ ] **Step 3: Implement update and query commands**

```python
def update_comment(repo: str, pr: int, comment_id: int,
                   status: str, summary: str = "") -> dict:
    """Update status of a specific comment. Returns updated tracker."""
    tracker = _load(repo, pr)
    for c in tracker["comments"]:
        if c["id"] == comment_id:
            c["status"] = status
            c["fix_summary"] = summary
            c["updated_at"] = datetime.now().isoformat()
            break
    _save(repo, pr, tracker)
    return tracker


def mark_replied(repo: str, pr: int, comment_id: int) -> None:
    """Mark a comment as having its reply sent."""
    tracker = _load(repo, pr)
    for c in tracker["comments"]:
        if c["id"] == comment_id:
            c["reply_sent"] = True
            c["updated_at"] = datetime.now().isoformat()
            break
    _save(repo, pr, tracker)


def get_pending(repo: str, pr: int) -> list:
    """Return comments with status 'pending'."""
    tracker = _load(repo, pr)
    return [c for c in tracker["comments"] if c["status"] == "pending"]


def get_summary(repo: str, pr: int) -> dict:
    """Return status counts."""
    tracker = _load(repo, pr)
    counts = {"total": 0, "pending": 0, "fixed": 0,
              "rejected": 0, "needs_user": 0}
    for c in tracker["comments"]:
        counts["total"] += 1
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    return counts
```

- [ ] **Step 4: Implement CLI interface**

```python
def main():
    parser = argparse.ArgumentParser(description="Review comment tracker")
    parser.add_argument("--repo", required=True, help="repo e.g. cann/hcomm")
    parser.add_argument("--pr", type=int, required=True, help="PR number")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sync", action="store_true", help="Sync comments from PR")
    group.add_argument("--update", type=int, metavar="COMMENT_ID",
                       help="Update comment status")
    group.add_argument("--pending", action="store_true",
                       help="Show pending comments")
    group.add_argument("--summary", action="store_true",
                       help="Show status counts")
    group.add_argument("--dump", action="store_true",
                       help="Dump full tracker JSON")

    parser.add_argument("--status", choices=["pending", "fixed", "rejected", "needs_user"],
                        help="New status (with --update)")
    parser.add_argument("--fix-summary", default="", help="Fix description (with --update)")

    args = parser.parse_args()

    if args.sync:
        tracker = sync(args.repo, args.pr)
        s = get_summary(args.repo, args.pr)
        print(json.dumps(s, indent=2))
    elif args.update:
        if not args.status:
            parser.error("--update requires --status")
        update_comment(args.repo, args.pr, args.update, args.status, args.fix_summary)
        print(f"Updated comment {args.update} -> {args.status}")
    elif args.pending:
        pending = get_pending(args.repo, args.pr)
        print(json.dumps(pending, indent=2, ensure_ascii=False))
    elif args.summary:
        s = get_summary(args.repo, args.pr)
        print(json.dumps(s, indent=2))
    elif args.dump:
        tracker = _load(args.repo, args.pr)
        print(json.dumps(tracker, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Test review_tracker.py**

```bash
cd /Users/shanshan/repo/_me/ai4hccl/skills/vibe-pr

# Verify it runs without errors
python3 scripts/review_tracker.py --repo cann/hcomm --pr 584 --sync
# Expected: JSON with status counts

python3 scripts/review_tracker.py --repo cann/hcomm --pr 584 --pending
# Expected: list of pending comments (may be empty if no review comments)

python3 scripts/review_tracker.py --repo cann/hcomm --pr 584 --summary
# Expected: {"total": N, "pending": N, "fixed": 0, "rejected": 0, "needs_user": 0}

python3 scripts/review_tracker.py --repo cann/hcomm --pr 584 --dump
# Expected: full tracker JSON
```

- [ ] **Step 6: Commit**

```bash
git add scripts/review_tracker.py
git commit -m "feat: add review_tracker.py for review comment status tracking"
```

---

### Task 2: ci_log_fetcher.py

**Files:**
- Create: `scripts/ci_log_fetcher.py`
- Read: `scripts/gitcode_api.py` (reuse get_token, get_pull_comments)
- Read: `scripts/comment_parser.py` (reference CI_RESULT_PATTERNS)

This script fetches CI failure info from PR comments (default) or CodeArts API (reserved).

- [ ] **Step 1: Create ci_log_fetcher.py with comment-based log extraction**

```python
#!/usr/bin/env python3
"""CI log fetcher for CANN PRs. Extracts CI results from PR comments or CodeArts API."""
import argparse
import json
import re
import sys
from pathlib import Path

scripts_dir = str(Path(__file__).parent)
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

import gitcode_api

# CI task names in CANN pipeline
CI_TASKS = {
    "compile", "codecheck", "codecheck_inc",
    "ut_test", "st_test", "build",
}

# Patterns to identify CI result comments from cann-robot
CI_COMMENT_PATTERNS = [
    re.compile(r"(compile|codecheck|codecheck_inc|ut_test|st_test|build)\s*[:\uff1a]\s*(pass|fail|success|failed|error)",
               re.IGNORECASE),
    re.compile(r"(pass|fail|success|failed|error)\s*[:\uff1a]?\s*(compile|codecheck|ut_test|st_test|build)",
               re.IGNORECASE),
]

# Pattern to extract log URLs
LOG_URL_PATTERN = re.compile(r"https?://[^\s\)]+(?:log|build|pipeline)[^\s\)]*", re.IGNORECASE)


def fetch_from_comments(repo: str, pr: int, task_filter: str = "all") -> dict:
    """Extract CI results from PR comments."""
    token = gitcode_api.get_token()
    comments = gitcode_api.get_pull_comments(repo, token, pr)

    tasks = []
    for comment in reversed(comments):  # newest first
        body = comment.get("body", "")
        author = comment.get("author", {})
        username = author.get("username", "") if isinstance(author, dict) else ""

        # Only look at bot comments
        if username not in ("cann-robot", "gitcode-bot", "ci-bot"):
            continue

        for pattern in CI_COMMENT_PATTERNS:
            for match in pattern.finditer(body):
                groups = match.groups()
                # Determine which group is task name vs status
                task_name = None
                status = None
                for g in groups:
                    g_lower = g.lower()
                    if g_lower in CI_TASKS:
                        task_name = g_lower
                    elif g_lower in ("pass", "success"):
                        status = "pass"
                    elif g_lower in ("fail", "failed", "error"):
                        status = "fail"

                if task_name and status:
                    if task_filter != "all" and task_name != task_filter:
                        continue
                    # Extract log URL if present
                    log_urls = LOG_URL_PATTERN.findall(body)
                    log_snippet = _extract_snippet(body, task_name)
                    tasks.append({
                        "name": task_name,
                        "status": status,
                        "log_url": log_urls[0] if log_urls else "",
                        "log_snippet": log_snippet,
                        "comment_id": comment.get("id"),
                    })

    # Deduplicate: keep only the latest result per task
    seen = set()
    deduped = []
    for t in tasks:
        if t["name"] not in seen:
            seen.add(t["name"])
            deduped.append(t)

    return {
        "source": "comments",
        "repo": repo,
        "pr": pr,
        "tasks": deduped,
    }


def _extract_snippet(body: str, task_name: str) -> str:
    """Try to extract a relevant error snippet from comment body."""
    lines = body.split("\n")
    relevant = []
    capture = False
    for line in lines:
        if task_name in line.lower() or "error" in line.lower() or "fail" in line.lower():
            capture = True
        if capture:
            relevant.append(line)
            if len(relevant) >= 20:
                break
    return "\n".join(relevant) if relevant else ""


def fetch_from_api(repo: str, pr: int, task_filter: str = "all") -> dict:
    """Fetch CI logs from CodeArts API. Reserved for future implementation."""
    raise NotImplementedError(
        "CodeArts API integration not yet implemented. "
        "Use --source comments as fallback."
    )


def fetch(repo: str, pr: int, source: str = "auto", task_filter: str = "all") -> dict:
    """Fetch CI results using specified source with fallback."""
    if source == "api":
        return fetch_from_api(repo, pr, task_filter)
    elif source == "comments":
        return fetch_from_comments(repo, pr, task_filter)
    else:  # auto
        try:
            return fetch_from_api(repo, pr, task_filter)
        except NotImplementedError:
            return fetch_from_comments(repo, pr, task_filter)
```

- [ ] **Step 2: Add CLI interface**

```python
def main():
    parser = argparse.ArgumentParser(description="Fetch CI logs for CANN PRs")
    parser.add_argument("--repo", required=True, help="repo e.g. cann/hcomm")
    parser.add_argument("--pr", type=int, required=True, help="PR number")
    parser.add_argument("--source", choices=["auto", "api", "comments"],
                        default="auto", help="Log source (default: auto)")
    parser.add_argument("--task", default="all",
                        help="Filter by task name (default: all)")

    args = parser.parse_args()
    result = fetch(args.repo, args.pr, args.source, args.task)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Test ci_log_fetcher.py**

```bash
cd /Users/shanshan/repo/_me/ai4hccl/skills/vibe-pr

python3 scripts/ci_log_fetcher.py --repo cann/hcomm --pr 584 --source comments
# Expected: JSON with tasks array, each having name/status/log_url/log_snippet

python3 scripts/ci_log_fetcher.py --repo cann/hcomm --pr 584 --source comments --task compile
# Expected: only compile task results

python3 scripts/ci_log_fetcher.py --repo cann/hcomm --pr 584 --source api
# Expected: NotImplementedError message (exit code 1)

python3 scripts/ci_log_fetcher.py --repo cann/hcomm --pr 584 --source auto
# Expected: falls back to comments, same as first test
```

- [ ] **Step 4: Commit**

```bash
git add scripts/ci_log_fetcher.py
git commit -m "feat: add ci_log_fetcher.py for CI log extraction from PR comments"
```

---

## Chunk 2: PR Dashboard

### Task 3: pr_dashboard.py

**Files:**
- Create: `scripts/pr_dashboard.py`
- Read: `scripts/pr_status.py` (reuse get_pr_status)
- Read: `scripts/task_context.py` (reuse context_path, CONTEXT_DIR)
- Read: `scripts/review_tracker.py` (reuse get_summary)

This script aggregates status of multiple active PRs into a single dashboard view.

- [ ] **Step 1: Create pr_dashboard.py with active PR discovery**

```python
#!/usr/bin/env python3
"""Multi-PR dashboard for CANN development."""
import argparse
import json
import os
import re
import sys
from pathlib import Path

scripts_dir = str(Path(__file__).parent)
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

import pr_status
import review_tracker

CONTEXT_DIR = Path.home() / ".claude" / "skills" / "vibe-pr" / "context"


def discover_active_prs() -> list:
    """Discover active PRs from context directory.
    Looks for files matching {repo}_{pr}.md pattern (task_context files).
    Returns list of (repo, pr) tuples.
    """
    prs = []
    if not CONTEXT_DIR.exists():
        return prs

    # task_context.py creates files like cann_hcomm_584.md
    pattern = re.compile(r"^(.+?)_(\d+)\.md$")
    for f in CONTEXT_DIR.iterdir():
        if f.suffix != ".md":
            continue
        m = pattern.match(f.name)
        if m:
            # Convert cann_hcomm back to cann/hcomm
            repo_parts = m.group(1)
            pr_num = int(m.group(2))
            # Heuristic: assume single underscore separates org/repo
            # e.g. cann_hcomm -> cann/hcomm
            parts = repo_parts.split("_", 1)
            if len(parts) == 2:
                repo = f"{parts[0]}/{parts[1]}"
                prs.append((repo, pr_num))
    return prs
```

- [ ] **Step 2: Implement dashboard data collection**

```python
def collect_pr_data(repo: str, pr: int) -> dict:
    """Collect status data for a single PR."""
    try:
        status = pr_status.get_pr_status(repo, pr)
    except Exception as e:
        return {
            "repo": repo, "pr": pr, "error": str(e),
            "title": "?", "cla": "?", "ci": "?",
            "review": "?", "next_action": "error",
        }

    # Review progress
    try:
        review_sum = review_tracker.get_summary(repo, pr)
        review_str = f"{review_sum['fixed']}/{review_sum['total']}"
    except Exception:
        review_str = "--"

    # Determine next action
    next_action = _infer_next_action(status, review_sum if review_str != "--" else None)

    return {
        "repo": repo.split("/")[-1],  # short name
        "pr": pr,
        "title": status.get("title", "")[:20],
        "cla": "ok" if status.get("cla") else "pend",
        "ci": status.get("ci", "--"),
        "review": review_str,
        "next_action": next_action,
    }


def _infer_next_action(status: dict, review_sum: dict = None) -> str:
    """Infer the most important next action for a PR."""
    if not status.get("cla"):
        return "sign CLA"
    ci = status.get("ci", "")
    if ci == "fail":
        return "fix CI"
    if ci in ("not_started", ""):
        return "trigger CI"
    if ci == "running":
        return "wait CI"

    # CI passed, check review
    lgtm = status.get("lgtm", {})
    approve = status.get("approve", {})
    if lgtm.get("got", 0) < lgtm.get("need", 1):
        return "find reviewer"
    if approve.get("got", 0) < approve.get("need", 1):
        return "get approve"

    if review_sum and review_sum.get("pending", 0) > 0:
        return f"fix {review_sum['pending']} comments"

    if status.get("merge_ready"):
        return "ready to merge"

    return "check status"
```

- [ ] **Step 3: Implement table output formatting**

```python
def format_table(rows: list) -> str:
    """Format PR data as a simple ASCII table."""
    if not rows:
        return "No active PRs found."

    headers = ["PR", "Repo", "Title", "CLA", "CI", "Review", "Next Action"]
    keys = ["pr", "repo", "title", "cla", "ci", "review", "next_action"]

    # Calculate column widths
    widths = [len(h) for h in headers]
    for row in rows:
        for i, k in enumerate(keys):
            val = str(row.get(k, ""))
            widths[i] = max(widths[i], len(val))

    def fmt_row(vals):
        return "| " + " | ".join(str(v).ljust(widths[i]) for i, v in enumerate(vals)) + " |"

    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    lines = [sep, fmt_row(headers), sep]
    for row in rows:
        vals = [row.get(k, "") for k in keys]
        lines.append(fmt_row(vals))
    lines.append(sep)

    return "\n".join(lines)


def format_json(rows: list) -> str:
    """Format PR data as JSON."""
    return json.dumps(rows, indent=2, ensure_ascii=False)
```

- [ ] **Step 4: Add CLI and main**

```python
def main():
    parser = argparse.ArgumentParser(description="CANN PR Dashboard")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--active", action="store_true",
                        help="Auto-discover active PRs from context/")
    source.add_argument("--prs", nargs="+", metavar="REPO:PR",
                        help="Specific PRs, e.g. cann/hcomm:584 cann/hccl:45")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON instead of table")

    args = parser.parse_args()

    if args.prs:
        pr_list = []
        for spec in args.prs:
            repo, num = spec.rsplit(":", 1)
            pr_list.append((repo, int(num)))
    elif args.active:
        pr_list = discover_active_prs()
    else:
        # Default to --active
        pr_list = discover_active_prs()

    if not pr_list:
        print("No active PRs found. Use --prs to specify, or create context with task_context.py --init")
        sys.exit(0)

    rows = []
    for repo, pr in pr_list:
        rows.append(collect_pr_data(repo, pr))

    if args.json:
        print(format_json(rows))
    else:
        print(format_table(rows))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Test pr_dashboard.py**

```bash
cd /Users/shanshan/repo/_me/ai4hccl/skills/vibe-pr

# Test with specific PR
python3 scripts/pr_dashboard.py --prs cann/hcomm:584
# Expected: ASCII table with one row

# Test with --json
python3 scripts/pr_dashboard.py --prs cann/hcomm:584 --json
# Expected: JSON array with one object

# Test auto-discovery
python3 scripts/pr_dashboard.py --active
# Expected: table with all PRs that have context files

# Test with no args (defaults to --active)
python3 scripts/pr_dashboard.py
```

- [ ] **Step 6: Commit**

```bash
git add scripts/pr_dashboard.py
git commit -m "feat: add pr_dashboard.py for multi-PR status overview"
```

---

## Chunk 3: Sub-agents

### Task 4: review-responder agent

**Files:**
- Create: `agents/review-responder.md`
- Create: `references/reply-templates.md`

- [ ] **Step 1: Create agents/ directory**

```bash
mkdir -p /Users/shanshan/repo/_me/ai4hccl/skills/vibe-pr/agents
```

- [ ] **Step 2: Create review-responder.md**

```markdown
---
name: review-responder
description: >
  Handle code review comments on CANN PRs. Reads related source code,
  applies coding standards, fixes issues, and replies to reviewers.
  Use when pr_monitor detects new review_suggestion or review_question
  comments that need processing.
model: inherit
tools:
  - Read
  - Edit
  - Grep
  - Glob
  - Bash
---

# Review Responder Agent

You are a code review response agent for CANN/HCCL open source projects.
You receive a batch of review comments and must fix code issues or draft replies.

## Input Format

You will receive a JSON prompt containing:
- `repo`: repository (e.g. "cann/hcomm")
- `pr`: PR number
- `repo_root`: local path to the repository checkout
- `comments`: array of review comments from comment_parser.py, each with:
  - `id`, `author`, `type` ("suggestion" or "question"), `body`, `file`, `line`

## Workflow

For each comment:

### If type == "suggestion"
1. Read the file mentioned in the comment. If no file specified, search the codebase
   using Grep/Glob based on code references in the comment body.
2. Read surrounding context (at least +-30 lines around the mentioned line).
3. Understand what the reviewer is asking to change.
4. Apply the fix using Edit tool.
5. Verify the fix makes sense in context (read the edited area).
6. Record result: add to `fixed` array with `comment_id`, `file`, `summary`, `reply_draft`.
7. Draft a concise reply: state what was changed and why. Be respectful.
   Use templates from references/reply-templates.md if available.

### If type == "question"
1. Read the relevant code to understand the context.
2. Analyze the question: what is the reviewer asking? Is it a design concern,
   a request for clarification, or a suggestion disguised as a question?
3. Draft a suggested reply with technical justification.
4. Record result: add to `needs_user` array with `comment_id`, `question`, `suggested_reply`.
5. Do NOT edit any files for questions.

## Quality Rules

- Do not make changes beyond what the reviewer asked for.
- If a suggestion contradicts existing project patterns, note this in the reply draft.
- If fixing one comment would conflict with another comment, process them in order
  and note the conflict.
- Maximum 10 comments per batch. If more are provided, process only the first 10
  and note the remainder.
- After all edits, run: `python3 scripts/review_tracker.py --repo {repo} --pr {pr} --update {id} --status fixed --fix-summary "{summary}"` for each fixed comment.
- For questions: `python3 scripts/review_tracker.py --repo {repo} --pr {pr} --update {id} --status needs_user`

## Output Format

Return a JSON summary at the end of your work:

```json
{
  "fixed": [
    {
      "comment_id": 123,
      "file": "src/foo.cc",
      "summary": "renamed variable per reviewer suggestion",
      "reply_draft": "Done. Renamed `old_name` to `new_name` to match the naming convention."
    }
  ],
  "needs_user": [
    {
      "comment_id": 456,
      "question": "Why not use shared_ptr here?",
      "suggested_reply": "We use raw pointer here because..."
    }
  ],
  "errors": []
}
```
```

- [ ] **Step 3: Create references/reply-templates.md**

```markdown
# Review Reply Templates

Templates for responding to code review comments on CANN PRs.
Used by the review-responder agent.

## Suggestion Accepted

> Done. {description of change}.

Example: "Done. Renamed `tmp_buf` to `recv_buffer` per naming convention."

## Suggestion Accepted with Modification

> Good catch. Applied a slightly different approach: {description}. Reason: {why}.

## Suggestion Declined (with reason)

> Thanks for the suggestion. Keeping the current approach because {reason}. {optional: alternative considered}.

## Question Response

> {Direct answer}. The rationale is {explanation}. See {file:line} for reference.

## Design Concern Response

> This is a good point. {acknowledgment}. The current design choice is based on {reason}. {optional: willing to discuss further / open to alternatives}.

## Tone Guidelines

- Always be respectful and grateful for the review.
- Keep replies concise - one or two sentences.
- Reference specific code locations when relevant.
- If declining a suggestion, always provide a clear technical reason.
- Use Chinese if the reviewer wrote in Chinese, English otherwise.
```

- [ ] **Step 4: Verify agent file is valid YAML frontmatter**

```bash
cd /Users/shanshan/repo/_me/ai4hccl/skills/vibe-pr
# Check YAML frontmatter parses correctly
python3 -c "
import yaml
with open('agents/review-responder.md') as f:
    content = f.read()
    # Extract frontmatter between --- markers
    parts = content.split('---', 2)
    if len(parts) >= 3:
        meta = yaml.safe_load(parts[1])
        print('Name:', meta.get('name'))
        print('Model:', meta.get('model'))
        print('Tools:', meta.get('tools'))
        print('VALID')
    else:
        print('ERROR: no frontmatter found')
"
# Expected: Name: review-responder, Model: inherit, Tools: [...], VALID
```

- [ ] **Step 5: Commit**

```bash
git add agents/review-responder.md references/reply-templates.md
git commit -m "feat: add review-responder agent and reply templates"
```

---

### Task 5: ci-analyzer agent

**Files:**
- Create: `agents/ci-analyzer.md`
- Create: `references/codecheck-rules.md`

- [ ] **Step 1: Create ci-analyzer.md**

```markdown
---
name: ci-analyzer
description: >
  Analyze CI failures on CANN PRs. Fetch logs from PR comments
  or CodeArts API, identify root cause, and suggest or apply fixes.
  Use when CI reports failure and automatic diagnosis is needed.
model: inherit
tools:
  - Read
  - Edit
  - Grep
  - Glob
  - Bash
---

# CI Analyzer Agent

You are a CI failure analysis agent for CANN/HCCL open source projects.
You diagnose CI failures and either fix them automatically or provide guidance.

## Input Format

You will receive a prompt containing:
- `repo`: repository (e.g. "cann/hcomm")
- `pr`: PR number
- `repo_root`: local path to the repository checkout
- `failed_tasks`: (optional) pre-fetched CI results from ci_log_fetcher.py

## Workflow

### Step 1: Fetch CI Results

If `failed_tasks` not provided:
```bash
python3 scripts/ci_log_fetcher.py --repo {repo} --pr {pr} --source auto
```

### Step 2: Analyze Each Failed Task

#### Compile Errors
1. Extract file path, line number, and error message from log_snippet.
2. Read the source file.
3. Understand the error (missing include, type mismatch, syntax error, etc.).
4. Fix using Edit tool.
5. If the error references a header or type from another file, Grep for it.
6. Mark as `auto_fixed`.

#### Static Check (codecheck / codecheck_inc)
1. Extract rule ID and file location from log_snippet.
2. Load references/codecheck-rules.md for rule explanation.
3. Apply the fix.
4. Mark as `auto_fixed`.

#### Test Failures (ut_test / st_test)
1. Identify the failing test case name.
2. Grep for the test in the codebase to find its source.
3. Determine if this is a regression (our change broke it) or flaky:
   - Read the test to understand what it asserts.
   - Check if our changed files are related to the test.
   - If unrelated: likely flaky -> mark as `needs_user`, suggest `/retest`.
   - If related: attempt to fix the regression.
4. Mark accordingly.

#### Unknown / No Detailed Log
1. Mark as `needs_user`.
2. Provide guidance: which CodeArts page to check, what to look for.

### Step 3: Verify Fixes

After all fixes, run a local syntax check if possible:
```bash
# For C++ files, try a quick grep for obvious issues
grep -rn "TODO\|FIXME\|HACK" {changed_files}
```

## Output Format

Return a JSON summary:
```json
{
  "auto_fixed": [
    {"task": "compile", "file": "src/foo.cc:42", "fix": "added missing #include"}
  ],
  "needs_user": [
    {"task": "ut_test", "reason": "flaky test (not related to our changes), suggest /retest"}
  ],
  "summary": "1 compile error fixed, 1 flaky test needs manual retest"
}
```

## Rules

- Only fix files that are part of the PR's changeset, or files that must change
  to fix a compile error introduced by our changes.
- If a fix requires changing test expectations, flag for user review rather than
  silently changing test assertions.
- If the same compile error appears in multiple files, fix all of them.
- Load references/codecheck-rules.md only when dealing with static check failures.
```

- [ ] **Step 2: Create references/codecheck-rules.md**

This file documents common static check rules encountered in CANN CI. Start with a skeleton that can be expanded as rules are encountered.

```markdown
# CANN Static Check Rules

Common rules flagged by codecheck / codecheck_inc in CANN CI pipeline.
Used by ci-analyzer agent to understand and fix violations.

## Naming Conventions

- **G.NAM.01**: Variable names must use camelCase (local) or snake_case (member).
- **G.NAM.02**: Function names must use CamelCase.
- **G.NAM.03**: Macro names must use ALL_CAPS_WITH_UNDERSCORES.
- **G.NAM.04**: Constants must use `k` prefix + CamelCase (e.g. `kMaxBufferSize`).

## Code Style

- **G.FMT.01**: Line length must not exceed 120 characters.
- **G.FMT.02**: Use 4 spaces for indentation, no tabs.
- **G.FMT.03**: Opening brace on same line for functions and control structures.
- **G.FMT.04**: No trailing whitespace.

## Safety

- **G.SEC.01**: Do not use `strcpy`, `sprintf` — use safe alternatives.
- **G.SEC.02**: Check return values of memory allocation.
- **G.SEC.03**: Validate pointer before dereference.

## Memory

- **G.MEM.01**: Match every `new` with `delete`, every `malloc` with `free`.
- **G.MEM.02**: Use smart pointers where ownership transfer occurs.
- **G.MEM.03**: Do not return references/pointers to local variables.

## Common HCCL-Specific Rules

- **H.COMM.01**: All public APIs must validate input parameters.
- **H.COMM.02**: Log level must match severity (ERROR for failures, INFO for normal).
- **H.COMM.03**: Thread-shared data must be protected by mutex or atomic.

---

**Note:** This file will be expanded as new rules are encountered in CI.
When a new rule ID appears in CI output that is not listed here, add it.
```

- [ ] **Step 3: Verify agent file**

```bash
cd /Users/shanshan/repo/_me/ai4hccl/skills/vibe-pr
python3 -c "
import yaml
with open('agents/ci-analyzer.md') as f:
    content = f.read()
    parts = content.split('---', 2)
    if len(parts) >= 3:
        meta = yaml.safe_load(parts[1])
        print('Name:', meta.get('name'))
        print('Model:', meta.get('model'))
        print('Tools:', meta.get('tools'))
        print('VALID')
"
# Expected: Name: ci-analyzer, Model: inherit, Tools: [...], VALID
```

- [ ] **Step 4: Commit**

```bash
git add agents/ci-analyzer.md references/codecheck-rules.md
git commit -m "feat: add ci-analyzer agent and codecheck rules reference"
```

---

## Chunk 4: PR Monitor Enhancement

### Task 6: Refactor pr_monitor.py to dispatcher pattern

**Files:**
- Modify: `scripts/pr_monitor.py` (324 lines -> ~280 lines)
- Read: `scripts/review_tracker.py` (import sync, update_comment)

The key change: pr_monitor stops calling `claude -p` directly. Instead it outputs structured task JSON that SKILL.md dispatches to sub-agents. The main loop collects new comments, syncs them to review_tracker, and outputs agent task payloads to stdout as JSON.

Current pr_monitor.py has `--once` mode at line 222-223 (already exists, no change needed).

Note on pr-dashboard: the design doc's architecture diagram shows a "pr-dashboard agent" box, but pr_dashboard.py is implemented as a pure script (Task 3) because it only aggregates data from existing scripts without needing LLM reasoning. No agent needed.

- [ ] **Step 1: Remove build_prompt and run_claude_fix functions (lines 108-158)**

Delete the entire `build_prompt` function (lines 108-125) and `run_claude_fix` function (lines 128-158). These are replaced by `generate_review_task` and `generate_ci_task` below.

- [ ] **Step 2: Add new imports and task generation functions**

At line 30, after existing imports, add:

```python
from comment_parser import parse_pr_comments
from gitcode_api import get_token, post_comment, GitCodeError
import review_tracker  # NEW
import ci_log_fetcher  # NEW
```

Replace the removed functions with:

```python
# ── Agent Task Generation ────────────────────────────────


def generate_review_task(comments, repo, pr, work_dir):
    """Generate a structured task for the review-responder agent."""
    return {
        "agent": "review-responder",
        "repo": repo,
        "pr": pr,
        "repo_root": work_dir,
        "comments": [
            {
                "id": c.get("id"),
                "author": c.get("author"),
                "type": c.get("type", "").replace("review_", ""),
                "body": c.get("body"),
                "file": c.get("file", ""),
                "line": c.get("line"),
            }
            for c in comments
        ],
    }


def generate_ci_task(repo, pr, work_dir):
    """Generate a structured task for the ci-analyzer agent."""
    ci_results = ci_log_fetcher.fetch(repo, pr, source="auto")
    return {
        "agent": "ci-analyzer",
        "repo": repo,
        "pr": pr,
        "repo_root": work_dir,
        "failed_tasks": ci_results,
    }


# ── Safety ───────────────────────────────────────────────

consecutive_failures = {}


def record_failure(comment_id):
    consecutive_failures[comment_id] = consecutive_failures.get(comment_id, 0) + 1


def should_escalate(comment_id):
    """Return True if this comment has failed fixes twice."""
    return consecutive_failures.get(comment_id, 0) >= 2


def count_changed_files(work_dir):
    """Count files changed in working tree."""
    r = subprocess.run(
        ["git", "diff", "--name-only"],
        cwd=work_dir, capture_output=True, text=True,
    )
    return len([l for l in r.stdout.strip().split("\n") if l])


# ── Adaptive Interval ────────────────────────────────────


def adaptive_interval(base_interval, last_activity_age_seconds):
    """Adjust polling interval based on recent activity."""
    if last_activity_age_seconds < 300:
        return max(60, base_interval // 3)
    elif last_activity_age_seconds > 3600:
        return min(600, base_interval * 2)
    return base_interval
```

- [ ] **Step 3: Add review_tracker.sync to get_new_comments**

Replace existing `get_new_comments` (lines 175-194) with:

```python
def get_new_comments(repo, pr, state, human_only, extra_bots):
    """Pull new comments, classify, sync to tracker. Returns (suggestions, questions)."""
    data = parse_pr_comments(repo, pr, since_commit=True)
    processed = set(state["processed_ids"])

    # Sync all comments to review tracker
    review_tracker.sync(repo, pr)

    suggestions, questions = [], []
    for c in data["review_comments"]:
        cid = c.get("id")
        if cid is not None and cid in processed:
            continue
        if human_only and extra_bots:
            if c["author"].lower() in {b.lower() for b in extra_bots}:
                continue

        # Skip comments that have already failed twice
        if should_escalate(cid):
            review_tracker.update_comment(repo, pr, cid, "needs_user",
                                          "Auto-fix failed twice, escalated")
            continue

        if c["type"] == "review_suggestion":
            suggestions.append(c)
        elif c["type"] == "review_question":
            questions.append(c)

    return suggestions, questions
```

- [ ] **Step 4: Rewrite main loop to output agent tasks instead of calling claude**

Replace the suggestion handling block in `main()` (lines 278-300) with:

```python
        # 处理 review_suggestion
        if suggestions:
            log(f"Found {len(suggestions)} suggestions")
            for s in suggestions:
                preview = s["body"][:120].replace("\n", " ")
                log(f"  - {s['author']}: {preview}")

            task = generate_review_task(suggestions, args.repo, args.pr, work_dir)
            print(json.dumps(task, ensure_ascii=False, indent=2), flush=True)

            # The agent task is now output to stdout as JSON.
            # SKILL.md dispatches this to the review-responder sub-agent.
            # After the agent completes and returns results, the main conversation:
            # 1. Calls review_tracker.update_comment() for each fixed/needs_user item
            # 2. If changes were made: commit_and_push, then trigger_ci
            # 3. If count_changed_files > 3: warn user before committing

            mark_processed(state, suggestions)
            state["fix_rounds"] += 1
            log(f"Round {state['fix_rounds']}/{args.max_rounds} task generated")
        else:
            if not questions:
                log("No new comments")
```

- [ ] **Step 5: Add --adaptive flag to CLI**

After the existing `--interval` argument (line 212), add:

```python
    ap.add_argument("--adaptive", action="store_true",
                    help="Adapt polling interval based on activity")
```

In the sleep section at the end of the main loop (lines 312-317), replace with:

```python
        # Adaptive or fixed interval
        interval = args.interval
        if args.adaptive:
            # Estimate time since last activity
            try:
                data = parse_pr_comments(args.repo, args.pr, since_commit=False)
                if data.get("review_comments"):
                    last = data["review_comments"][-1].get("created_at", "")
                    from comment_parser import parse_datetime
                    age = (datetime.now() - parse_datetime(last)).total_seconds()
                    interval = adaptive_interval(args.interval, age)
            except Exception:
                pass

        log(f"Waiting {interval}s...")
        for _ in range(interval):
            if not running[0]:
                break
            time.sleep(1)
```

- [ ] **Step 6: Test pr_monitor.py changes**

```bash
cd /Users/shanshan/repo/_me/ai4hccl/skills/vibe-pr

# Test that old claude -p code path is removed
grep -n "claude.*-p\|run_claude_fix\|build_prompt" scripts/pr_monitor.py
# Expected: no matches

# Test --once mode outputs JSON task
python3 scripts/pr_monitor.py --repo cann/hcomm --pr 584 --once 2>/dev/null
# Expected: JSON with "agent": "review-responder" if new comments exist, or "No new comments" log

# Test generate_review_task output format
python3 -c "
import sys; sys.path.insert(0, 'scripts')
from pr_monitor import generate_review_task
task = generate_review_task([{'id':1,'body':'test','author':'x','type':'review_suggestion'}], 'cann/hcomm', 584, '.')
import json; print(json.dumps(task, indent=2))
"
# Expected: dict with agent, repo, pr, repo_root, comments keys

# Test --help shows new --adaptive flag
python3 scripts/pr_monitor.py --help | grep adaptive
# Expected: --adaptive line shown
```

- [ ] **Step 7: Commit**

```bash
git add scripts/pr_monitor.py
git commit -m "refactor: transform pr_monitor.py from fixer to dispatcher pattern"
```

---

## Chunk 5: SKILL.md Progressive Disclosure Refactor

### Task 7: Extract phase details to reference files

**Files:**
- Create: `references/phase-0-requirements.md` (from SKILL.md lines 62-101)
- Create: `references/phase-1-selfcheck.md` (from SKILL.md lines 123-155)
- Create: `references/phase-4-ci.md` (from SKILL.md lines 225-297, enhanced with new agent/script)
- Create: `references/phase-5-reviewer.md` (from SKILL.md lines 300-429, enhanced with new agent/script)

- [ ] **Step 1: Create references/phase-0-requirements.md**

Write this file with the following content (extracted from SKILL.md Phase 0, kept verbatim with minor formatting):

```markdown
# Phase 0: Requirements

From issue or user-described requirements: analyze, locate code, generate change plan, confirm with user.

## 0.1 Get Requirements

If from issue:
\```bash
python3 ~/.claude/skills/vibe-pr/scripts/issue_parser.py --repo cann/hcomm --issue 123
# or URL
python3 ~/.claude/skills/vibe-pr/scripts/issue_parser.py --url https://gitcode.com/cann/hcomm/issues/123
\```

Output JSON contains: title, description, labels, assignees, comments.

If from user: extract key information directly from conversation.

## 0.2 Analyze Requirements

Extract from the requirement:
- Problem symptom (what's broken / what feature is missing)
- Reproduction conditions (if bug)
- Expected behavior (what it should look like after fix)

## 0.3 Locate Code

Clone/update the target repo (if not local), locate relevant code:
- Search by error messages, function names, module names
- Read code to understand existing logic
- Identify files that need modification

## 0.4 Generate Change Plan

Present change plan to user:
- List of files to modify
- Intent of each modification
- Risk points (potential impact on other modules)

Wait for user confirmation before starting development.
Exception: trivial bugs (one-line fix, typo, obvious copy-paste error) can go straight to PR, notify after.
```

- [ ] **Step 2: Create references/phase-1-selfcheck.md**

```markdown
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
```

- [ ] **Step 3: Create references/phase-4-ci.md**

```markdown
# Phase 4: Trigger CI and Handle Failures

## Trigger CI

CI does not auto-trigger. Post `compile` in PR comments:

\```bash
python3 -c "
from gitcode_api import get_token, post_comment
post_comment('<repo>', get_token(), <pr_number>, 'compile')
"
\```

CI runs ~20-30 minutes. On pass: label `ci-pipeline-passed` added automatically.
Each new push removes `ci-pipeline-passed` -- must re-trigger CI.

Check CI status:
\```bash
python3 ~/.claude/skills/vibe-pr/scripts/pr_status.py --repo cann/hcomm --pr <number>
\```

## CI Failure Handling

On failure: attempt self-diagnosis and fix before escalating to user.

### 4.1 Get Failure Info

Use ci_log_fetcher.py to extract CI results:
\```bash
python3 ~/.claude/skills/vibe-pr/scripts/ci_log_fetcher.py --repo cann/hcomm --pr <number> --source auto
\```

If ci_log_fetcher returns insufficient info, ask user to paste log snippet from CodeArts web UI.

For complex failures, dispatch the ci-analyzer agent:
\```
Agent dispatch: ci-analyzer
Input: {"repo": "<repo>", "pr": <number>, "repo_root": "<path>"}
\```

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
```

- [ ] **Step 4: Create references/phase-5-reviewer.md**

```markdown
# Phase 5: Find Reviewer + Respond to Reviews

## 5.1 Understand the Landscape

\```bash
python3 ~/.claude/skills/vibe-pr/scripts/pr_status.py --repo cann/hcomm --pr <number>
\```

Get: candidate list (`modules[].lgtm.candidates` / `approve.candidates`), current approval progress, latest commit time.

\```bash
python3 ~/.claude/skills/vibe-pr/scripts/reviewer_activity.py --repo cann/hcomm --pr <number> --recent 30
\```

Activity data is background info, not a shopping list:
- High review count = this person is already busy, not that they're always available
- Low review count != unwilling to help, maybe just nobody asked
- Fast response means they value the community, not that they're idle

## 5.2 Relationships Before Rankings

Ask user (proactively if they don't mention):
- Who have you reviewed code for before? (reciprocity is the strongest ask)
- Who have you interacted with at SIG meetings or mailing lists?
- Anyone who has reviewed for you before? (maintaining relationships > building new ones)

For new contributors with no community ties: suggest reviewing others' PRs first, showing up at SIG meetings, then asking for reviews. This is investment, not wasted time.

## 5.3 Recommendation Strategy

Combine relationships and data:
- lgtm needs 2, recommend 3 for redundancy; approve needs 1, recommend 2
- Prioritize candidates with existing interaction history
- Then look at who has context on this code module (from recent PRs)
- Activity ranking is last resort

## 5.4 How to Request Review

Principles for user:
- Don't mass-@. Two or three people is normal, five is spam
- Explain what changed and why. Reviewer's time > your wait time
- Keep PRs small. Nobody wants to review 500 lines; 50 lines gets approved quickly
- Timing: hccl SIG biweekly Fri 14:00 meeting; before/after meeting is good timing; don't push on weekends/holidays

If no response for a long time, ask yourself:
1. Is the PR too big? Can it be split?
2. Is the description clear enough? Can a reviewer understand in 30 seconds?
3. Have you reviewed for others recently?

## 5.5 Approval Rules

- Each module needs at least 2 `/lgtm` + 1 `/approve`
- `/approve` implies `/lgtm`
- Approval must be after latest commit time (new push invalidates old approvals)
- pr_status.py output `latest_commit_at` can check approval validity

Update context: record recommended reviewers and reasoning.

---

## 5.5 (bis): Review Response

When reviewer leaves comments, classify and act.

### Get Review Comments

\```bash
# All review comments
python3 ~/.claude/skills/vibe-pr/scripts/comment_parser.py --repo cann/hcomm --pr <number>

# Only after latest push
python3 ~/.claude/skills/vibe-pr/scripts/comment_parser.py --repo cann/hcomm --pr <number> --since-commit
\```

Check review status board:
\```bash
python3 ~/.claude/skills/vibe-pr/scripts/review_tracker.py --repo cann/hcomm --pr <number> --summary
python3 ~/.claude/skills/vibe-pr/scripts/review_tracker.py --repo cann/hcomm --pr <number> --pending
\```

### Dispatch review-responder agent

For batches of review comments, dispatch the review-responder agent:
\```
Agent dispatch: review-responder
Input: {
  "repo": "<repo>",
  "pr": <number>,
  "repo_root": "<path>",
  "comments": [comment_parser output]
}
\```

The agent returns:
- `fixed`: comments it resolved (with fix summary and reply drafts)
- `needs_user`: questions/design concerns (with suggested replies)

### After Agent Returns

- Show user the `fixed` summaries and ask to batch-send replies
- Show user the `needs_user` questions with suggested replies for editing
- If files were changed: commit, push, re-trigger CI
- Update review_tracker status for each comment

### Important Notes

- New push invalidates prior lgtm/approve -- need to re-request review
- Do not post replies on behalf of user (social actions must be by the user)
- Prepare reply content for user to copy/edit before sending
```

- [ ] **Step 5: Commit reference files**

```bash
git add references/phase-0-requirements.md references/phase-1-selfcheck.md \
       references/phase-4-ci.md references/phase-5-reviewer.md
git commit -m "docs: extract phase details to reference files for progressive disclosure"
```

---

### Task 8: Rewrite SKILL.md (slim version)

**Files:**
- Modify: `SKILL.md` (529 -> ~185 lines)

- [ ] **Step 1: Replace SKILL.md with complete new content**

Write the entire file (do NOT use placeholders; this is the final content):

````markdown
---
name: vibe-pr
description: "CANN 社区自主开发 bot。能接需求、自主开发、自我检视、管理 PR 全生命周期。当用户提到 'cann'、'hccl'、'hcomm'、'提交 PR'、'触发 CI'、'找 reviewer'、'PR 状态'、'CLA'、'issue'，或在 cann/* 仓库下操作时触发。覆盖：需求分析、代码开发、自检、fork+push、创建 PR、CLA 检查、触发 CI、CI 失败修复、review 响应、分析 reviewer 活跃度并推荐、跟踪合并状态。"
---

# CANN 社区自主开发 Bot

从需求到合并的完整闭环：

```
issue → 理解需求 → 定位代码 → 写代码 → 自检 → 提交 PR
  ↑                                                    ↓
  │                                              触发 CI
  │                                                    ↓
  │                                         CI 通过？──否──→ ci-analyzer agent ──┐
  │                                              ↓ 是                           │
  │                                         请求 review                    重新提交 ←┘
  │                                              ↓
  │                                    reviewer 有意见？──是──→ review-responder agent ──┐
  │                                              ↓ 否                                   │
  │                                         四标签就位                              重新提交 ←┘
  │                                              ↓
  └──────────────────────────────── squash merge ← /check-pr
```

自治边界——什么该自动，什么该问人：
- 全自动：读 issue、定位代码、按规范写代码、自检、创建 PR、触发 CI、CI 失败后修编译错误/静态检查问题、查 PR 状态
- 需要确认：需求理解是否正确、代码改动方案、响应 reviewer 的设计层面质疑、CI 失败原因不明时的修复策略
- 必须人来：选择找谁 review（人情世故）、在 PR 评论区 @ reviewer、处理与 reviewer 的分歧、CLA 签署、决定是否 force push

详细 workflow 参考见 [references/workflow.md](references/workflow.md)。

脚本目录：`~/.claude/skills/vibe-pr/scripts/`
API 封装：`gitcode_api.py`（认证、GET/POST、分页全在里面，直接 import 使用）

---

## 跨 session 上下文

每个活跃 PR 有一个 context 文件（`~/.claude/skills/vibe-pr/context/`）记录工作进度和决策。

session 开始时，先读取 context：
```bash
python3 ~/.claude/skills/vibe-pr/scripts/task_context.py --repo <repo> --pr <number>
```

没有 context 文件时，用 `--init` 从当前 PR 状态生成：
```bash
python3 ~/.claude/skills/vibe-pr/scripts/task_context.py --repo <repo> --pr <number> --init
```

不确定在处理哪个 PR 时，列出所有活跃 context：
```bash
python3 ~/.claude/skills/vibe-pr/scripts/task_context.py --list
```

里程碑更新：阶段推进、重要决策、遇到阻塞时，用 Edit 工具更新 context 文件。
只记录跨 session 会丢失的信息（用户确认的方案、reviewer 关系、失败原因），不记录可从 API 重新获取的信息。

---

## Phases

| Phase | Name | Trigger | Details |
|-------|------|---------|---------|
| 0 | 需求接入 | New issue or user request | @references/phase-0-requirements.md |
| 1 | Fork + Push | Code ready | @references/workflow.md (Fork + Push section) |
| 1.5 | 自检 | Before PR creation | @references/phase-1-selfcheck.md |
| 2 | 创建 PR | Code pushed | See below |
| 3 | CLA 检查 | PR created | See below |
| 4 | 触发 CI | CLA signed | @references/phase-4-ci.md |
| 5 | 找 Reviewer + Review 响应 | CI passed | @references/phase-5-reviewer.md |
| 6 | 跟踪状态 | Reviewer assigned | See below |

---

## Agent Dispatch

| Situation | Agent/Script | Input |
|-----------|-------------|-------|
| New review suggestions/questions | review-responder agent | comment_parser output (JSON) |
| CI failure | ci-analyzer agent | ci_log_fetcher output (JSON) |
| Multi-PR overview | pr_dashboard.py (script) | -- |
| Review status check | review_tracker.py (script) | -- |
| Monitor PR for new comments | pr_monitor.py --once (script) | -- |

When dispatching an agent, pass repo, pr, repo_root, and relevant data as JSON prompt.

---

## Phase 1: Fork + Push

1. Confirm target repo (e.g. `cann/hcomm`) and branch name
2. Check fork remote: `git remote -v`
3. No fork? `gitcode_api.create_fork(repo, token)`
4. Add remote and push:
   ```bash
   git remote add fork https://gitcode.com/<username>/hcomm.git
   git push -u fork <branch-name>
   ```

Token from `~/.git-credentials` (format `https://username:token@gitcode.com`), use `gitcode_api.get_token()`.

---

## Phase 2: Create PR

先检查 push 时是否已自动创建 MR：
```python
pulls = list_pulls('<repo>', get_token(), state='open', head='<username>:<branch>')
```

已有 PR → `update_pull(repo, token, number, title='...', body='...')`

没有 → `create_pull(repo, token, title, head, base, body)`
- head 格式：`"用户名:分支名"`（如 `fan33:fix/xxx`）
- base 通常是 `master`
- 返回 409 时从错误信息提取 MR 编号 `!NNN`，改用 `update_pull`

---

## Phase 3: CLA Check

PR 创建后 cann-robot 秒级自动检查。如果失败：
1. 检查 commit 的 committer email 是否与 CLA 签署邮箱一致
2. 修正并 force push：
   ```bash
   git -c user.email="cla-email@example.com" commit --amend --reset-author --no-edit
   git push fork <branch> --force
   ```
3. 评论 `/check-cla` 重新检查

CLA 签署页面：https://clasign.osinfra.cn/sign/68cbd4a3dbabc050b436cdd4
CLA 只能在浏览器签署，无法 API 代签。

---

## Phase 6: Track Status

```bash
python3 ~/.claude/skills/vibe-pr/scripts/pr_status.py --repo cann/hcomm --pr <number>
```

四个标签全部就位时 cann-robot 自动 squash merge：
- `cann-cla/yes` — CLA
- `ci-pipeline-passed` — CI
- `lgtm` — 代码审查
- `approved` — 合并授权

长时间无人 review：先检查 PR 大小和描述质量，考虑 SIG 例会（双周五 14:00）提一嘴，或邮件列表 hccl@cann.osinfra.cn 发简短说明。

---

## Script Reference

| Script | Purpose | Example |
|--------|---------|---------|
| gitcode_api.py | API layer | (imported by other scripts) |
| issue_parser.py | Parse issue | `python3 scripts/issue_parser.py --repo X --issue N` |
| pr_status.py | PR status | `python3 scripts/pr_status.py --repo X --pr N` |
| comment_parser.py | Classify comments | `python3 scripts/comment_parser.py --repo X --pr N --since-commit` |
| reviewer_activity.py | Reviewer analysis | `python3 scripts/reviewer_activity.py --repo X --pr N --recent 30` |
| task_context.py | Cross-session context | `python3 scripts/task_context.py --repo X --pr N --init` |
| review_tracker.py | Review status board | `python3 scripts/review_tracker.py --repo X --pr N --summary` |
| ci_log_fetcher.py | CI log extraction | `python3 scripts/ci_log_fetcher.py --repo X --pr N --source auto` |
| pr_dashboard.py | Multi-PR dashboard | `python3 scripts/pr_dashboard.py --active` |
| pr_monitor.py | Monitor + dispatch | `python3 scripts/pr_monitor.py --repo X --pr N --once` |

---

## Merge Troubleshooting

| Symptom | Fix |
|---------|-----|
| squash conflict | rebase target branch, re-push |
| concurrent merge conflict | wait 1 min, comment `/check-pr` |
| title has `[WIP]` | remove `[WIP]` prefix |
| unresolved review threads | resolve all CodeReview discussions |
| CI retry button fails | re-comment `/compile` for full re-run |

Details in [references/workflow.md](references/workflow.md).

---

## Bot Commands

| Command | Function |
|---------|----------|
| `/compile` | Trigger CI |
| `/lgtm` / `/lgtm cancel` | Approve review / revoke |
| `/approve` / `/approve cancel` | Approve merge / revoke |
| `/check-cla` | Re-check CLA |
| `/check-pr` | Check labels and trigger merge |
| `/assign @user` | Assign issue |
| `/close` | Close issue |
````

- [ ] **Step 2: Verify SKILL.md line count is under 200**

```bash
wc -l SKILL.md
# Expected: ~185 lines (under 200)
```

- [ ] **Step 3: Verify all references/ pointers resolve to existing files**

```bash
cd /Users/shanshan/repo/_me/ai4hccl/skills/vibe-pr
for ref in references/phase-0-requirements.md references/phase-1-selfcheck.md \
           references/phase-4-ci.md references/phase-5-reviewer.md \
           references/workflow.md references/codecheck-rules.md \
           references/reply-templates.md; do
    if [ -f "$ref" ]; then
        echo "OK: $ref ($(wc -l < "$ref") lines)"
    else
        echo "MISSING: $ref"
    fi
done
# Expected: all OK, no MISSING
```

- [ ] **Step 4: Verify no critical content was lost**

Compare old vs new SKILL.md to ensure all phases are either inline or referenced:

```bash
# Check that all 7 phases are mentioned
grep -c "Phase\|阶段" SKILL.md
# Expected: >= 7

# Check that all 10 scripts are in the reference table
grep -c "scripts/" SKILL.md
# Expected: >= 10
```

- [ ] **Step 5: Commit**

```bash
git add SKILL.md
git commit -m "refactor: slim SKILL.md from 529 to ~185 lines with progressive disclosure"
```

---

## Chunk 6: Integration Verification

### Task 9: End-to-end smoke test

**Files:** None (testing only)

- [ ] **Step 1: Verify all scripts run without import errors**

```bash
cd /Users/shanshan/repo/_me/ai4hccl/skills/vibe-pr

for script in scripts/*.py; do
    echo "--- $script ---"
    python3 "$script" --help 2>&1 | head -3
    echo ""
done
# Expected: each script shows its argparse help without import errors
```

- [ ] **Step 2: Verify agent files have valid frontmatter**

```bash
for agent in agents/*.md; do
    echo "--- $agent ---"
    python3 -c "
import yaml
with open('$agent') as f:
    parts = f.read().split('---', 2)
    meta = yaml.safe_load(parts[1])
    print('name:', meta.get('name'))
    print('tools:', meta.get('tools'))
    print('VALID')
"
done
# Expected: both agents show VALID
```

- [ ] **Step 3: Verify reference files exist and are non-empty**

```bash
for ref in references/*.md; do
    lines=$(wc -l < "$ref")
    echo "$ref: $lines lines"
done
# Expected: all files have >10 lines
```

- [ ] **Step 4: Run a real sync+dashboard flow**

```bash
# Sync a real PR's review comments
python3 scripts/review_tracker.py --repo cann/hcomm --pr 584 --sync
python3 scripts/review_tracker.py --repo cann/hcomm --pr 584 --summary

# Run dashboard
python3 scripts/pr_dashboard.py --prs cann/hcomm:584

# Fetch CI info
python3 scripts/ci_log_fetcher.py --repo cann/hcomm --pr 584 --source comments
```

- [ ] **Step 5: Verify SKILL.md structure is loadable**

```bash
cd /Users/shanshan/repo/_me/ai4hccl/skills/vibe-pr

# Check YAML frontmatter parses
python3 -c "
import yaml
with open('SKILL.md') as f:
    parts = f.read().split('---', 2)
    meta = yaml.safe_load(parts[1])
    print('name:', meta.get('name'))
    desc = meta.get('description', '')
    print('description length:', len(desc))
    # Verify trigger words are present
    triggers = ['cann', 'hccl', 'hcomm', 'PR', 'CI', 'reviewer', 'CLA', 'issue']
    for t in triggers:
        assert t.lower() in desc.lower(), f'Missing trigger: {t}'
    print('All trigger words present')
    print('VALID')
"
# Expected: name, description length, all triggers present, VALID
```

- [ ] **Step 6: Verify .gitignore excludes runtime files**

Check that context/ and state/ directories' runtime JSON files are gitignored:

```bash
cat .gitignore
# Should contain entries for context/*.json and state/
# If not, add them:
# echo 'context/*.json' >> .gitignore
# echo 'state/' >> .gitignore
```

- [ ] **Step 7: Final commit (if any test-driven fixes needed)**

```bash
git add -A
git status
# Only commit if there are fixes from testing
git commit -m "fix: address integration test issues"
```
