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

# Actual CI task names in CANN pipeline (from cann-robot HTML table)
CI_TASKS = {
    "codecheck", "sca", "anti_virus", "check_pr",
    "compile_ascend_x86", "compile_ascend_arm",
    "api_check", "pre_comment",
    "ut_test", "st_test", "smoke_a900",
}

# Pattern to parse HTML table rows from cann-robot CI comments.
# Real format:
#   <td><strong>codecheck</strong></td>
#   <td>✅ SUCCESS</td>
#   <td><a href=URL>>>>>></a></td>
#   <td><a href=URL>>>>>></a></td>
CI_TABLE_ROW = re.compile(
    r"<td><strong>(.+?)</strong></td>\s*"
    r"<td>\s*(?:✅|❌|⚠️|🔴|🟢)?\s*(SUCCESS|FAILED|RUNNING|ERROR|TIMEOUT|ABORTED)\s*</td>"
    r'(?:\s*<td><a\s+href="?([^">\s]*)"?>.*?</a></td>)?',
    re.IGNORECASE | re.DOTALL,
)

# Fallback: detect CI trigger comment (no results table yet)
CI_TRIGGER_PATTERN = re.compile(r"流水线任务触发成功")


def fetch_from_comments(repo: str, pr: int, task_filter: str = "all") -> dict:
    """Extract CI results from the latest cann-robot HTML table comment."""
    token = gitcode_api.get_token()
    comments = gitcode_api.get_pull_comments(repo, token, pr)

    # Walk newest-first to find the latest CI result table
    for comment in reversed(comments):
        body = comment.get("body", "")
        author = comment.get("user", {})
        username = author.get("login", "") if isinstance(author, dict) else ""

        if username != "cann-robot":
            continue

        # Only parse comments with an HTML table (CI result)
        if "<table" not in body:
            continue

        tasks = []
        for match in CI_TABLE_ROW.finditer(body):
            task_name = match.group(1).strip()
            raw_status = match.group(2).strip().upper()
            log_url = match.group(3).strip() if match.group(3) else ""

            task_key = task_name.lower()

            if raw_status == "SUCCESS":
                status = "pass"
            elif raw_status in ("FAILED", "ERROR", "TIMEOUT", "ABORTED"):
                status = "fail"
            elif raw_status == "RUNNING":
                status = "running"
            else:
                status = raw_status.lower()

            if task_filter != "all" and task_filter.lower() != task_key:
                continue

            tasks.append({
                "name": task_name,
                "status": status,
                "log_url": log_url,
                "comment_id": comment.get("id"),
            })

        if tasks:
            return {
                "source": "comments",
                "repo": repo,
                "pr": pr,
                "tasks": tasks,
            }

    return {
        "source": "comments",
        "repo": repo,
        "pr": pr,
        "tasks": [],
    }


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
