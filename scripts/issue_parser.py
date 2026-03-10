#!/usr/bin/env python3
"""从 GitCode 拉取 issue 详情，结构化输出。

用法：
    python issue_parser.py --repo cann/hcomm --issue 123
    python issue_parser.py --url https://gitcode.com/cann/hcomm/issues/123
"""

import argparse
import json
import re
import sys

from gitcode_api import get_token, api_get, api_get_paginated, GitCodeError


def parse_issue_url(url):
    """从 issue URL 中提取 owner/repo 和 issue 编号。

    支持格式：
        https://gitcode.com/cann/hcomm/issues/123
        https://gitcode.com/cann/hcomm/-/issues/123
    """
    match = re.search(r"gitcode\.com/([^/]+/[^/]+?)(?:/-)?/issues/(\d+)", url)
    if not match:
        return None, None
    return match.group(1), int(match.group(2))


def get_issue(repo, token, number):
    """获取 issue 详情。"""
    owner, name = repo.split("/")
    return api_get(f"repos/{owner}/{name}/issues/{number}", token)


def get_issue_comments(repo, token, number):
    """获取 issue 的所有评论。"""
    owner, name = repo.split("/")
    return api_get_paginated(
        f"repos/{owner}/{name}/issues/{number}/comments", token
    )


def parse_issue(repo, number):
    """拉取并结构化 issue 信息。

    返回 JSON：
    {
        "repo": "cann/hcomm",
        "number": 123,
        "title": "...",
        "state": "open",
        "author": "username",
        "assignees": ["user1", "user2"],
        "labels": ["bug", "priority/high"],
        "description": "issue body ...",
        "comments": [
            {"author": "user1", "body": "...", "created_at": "..."}
        ],
        "created_at": "...",
        "updated_at": "..."
    }
    """
    token = get_token()
    issue = get_issue(repo, token, number)

    # 提取标签名
    labels = [lb["name"] for lb in issue.get("labels", [])]

    # 提取指派人
    assignees = []
    if issue.get("assignee"):
        assignees.append(issue["assignee"].get("login", ""))
    # GitCode 可能在 assignees 字段提供多人
    for a in issue.get("assignees", []):
        login = a.get("login", "")
        if login and login not in assignees:
            assignees.append(login)

    # 获取评论
    comments_raw = get_issue_comments(repo, token, number)
    comments = []
    for c in comments_raw:
        comments.append({
            "author": c.get("user", {}).get("login", ""),
            "body": c.get("body", ""),
            "created_at": c.get("created_at", ""),
        })

    return {
        "repo": repo,
        "number": number,
        "title": issue.get("title", ""),
        "state": issue.get("state", ""),
        "author": issue.get("user", {}).get("login", ""),
        "assignees": assignees,
        "labels": labels,
        "description": issue.get("body", ""),
        "comments": comments,
        "created_at": issue.get("created_at", ""),
        "updated_at": issue.get("updated_at", ""),
    }


def main():
    parser = argparse.ArgumentParser(description="获取 GitCode issue 详情")
    parser.add_argument("--repo", help="仓库，如 cann/hcomm")
    parser.add_argument("--issue", type=int, help="Issue 编号")
    parser.add_argument("--url", help="Issue URL（与 --repo/--issue 二选一）")
    args = parser.parse_args()

    # 确定 repo 和 issue 编号
    if args.url:
        repo, number = parse_issue_url(args.url)
        if not repo:
            print(f"错误：无法解析 URL：{args.url}", file=sys.stderr)
            sys.exit(1)
    elif args.repo and args.issue:
        repo, number = args.repo, args.issue
    else:
        print("错误：必须指定 --url 或同时指定 --repo 和 --issue", file=sys.stderr)
        sys.exit(1)

    try:
        result = parse_issue(repo, number)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except GitCodeError as e:
        print(f"错误：{e.message}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
