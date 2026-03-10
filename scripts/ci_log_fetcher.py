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
        author = comment.get("user", {})
        username = author.get("login", "") if isinstance(author, dict) else ""

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
