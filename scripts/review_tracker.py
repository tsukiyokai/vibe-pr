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
