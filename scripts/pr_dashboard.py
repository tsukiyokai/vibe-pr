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
    review_sum = None
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
    if ci == "failed":
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
