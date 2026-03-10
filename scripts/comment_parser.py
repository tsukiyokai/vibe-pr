#!/usr/bin/env python3
"""解析 PR 评论，区分类型，提取 review 意见。

用法：
    python comment_parser.py --repo cann/hcomm --pr 584
    python comment_parser.py --repo cann/hcomm --pr 584 --since-commit  # 只看最新 push 之后的评论
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone

from gitcode_api import get_token, get_pull, get_pull_comments, api_get, GitCodeError


# cann-robot 和常见 bot 账号
BOT_ACCOUNTS = {"cann-robot", "gitcode-bot", "cla-assistant"}

# bot 命令模式（评论以这些开头视为 bot 命令，不是 review 意见）
BOT_COMMAND_PATTERNS = [
    r"^/?(compile|check-cla|check-pr|lgtm|approve|merge|assign|unassign|close|kind|remove-kind|priority|sig)\b",
]

# CI 结果评论的特征
CI_RESULT_PATTERNS = [
    r"ci-pipeline",
    r"流水线",
    r"pipeline.*(?:passed|failed|running)",
    r"compile.*(?:success|fail)",
    r"\|.*task.*\|.*status.*\|",  # 表格形式的 CI 结果
]


def classify_comment(comment, pr_author):
    """对单条评论分类。

    返回类型：
    - "bot_auto": bot 自动生成的评论（欢迎、CI 结果、CLA 检查等）
    - "bot_command": 人发的 bot 命令（/lgtm, /compile 等）
    - "ci_result": CI 结果通知
    - "review_suggestion": reviewer 的代码修改建议
    - "review_question": reviewer 的提问或设计质疑
    - "author_reply": PR 作者的回复
    - "other": 无法归类
    """
    author = comment.get("user", {}).get("login", "")
    body = comment.get("body", "").strip()

    if not body:
        return "other"

    # bot 账号的评论
    if author.lower() in {b.lower() for b in BOT_ACCOUNTS}:
        # 区分 CI 结果和其他 bot 评论
        for pattern in CI_RESULT_PATTERNS:
            if re.search(pattern, body, re.IGNORECASE):
                return "ci_result"
        return "bot_auto"

    # bot 命令（任何人发的）
    for pattern in BOT_COMMAND_PATTERNS:
        if re.match(pattern, body, re.IGNORECASE):
            return "bot_command"

    # 结构化 AI Code Review：不管谁发的，内容是 review 就按 review 处理
    if _is_ai_code_review(body):
        return "review_suggestion"

    # PR 作者自己的评论
    if author == pr_author:
        return "author_reply"

    # reviewer 的评论——区分建议和提问
    # 1) 明确的建议关键词优先（不含代码块判断）
    if _has_suggestion_keywords(body):
        return "review_suggestion"
    # 2) 然后检查提问
    if _is_question(body):
        return "review_question"
    # 3) 代码块作为 suggestion 的弱信号（fallback）
    if "```" in body:
        return "review_suggestion"

    # 默认：reviewer 的一般评论，按 suggestion 处理（宁多勿漏）
    return "review_suggestion"


def _is_ai_code_review(body):
    """判断是否是结构化的 AI Code Review 评论。

    特征：以 '## AI Code Review' 开头，或包含 REVIEWED_SHA 标记。
    这类评论即使由 PR 作者发出，内容也是 review 性质，应当作 review_suggestion 处理。
    """
    return (
        body.startswith("## AI Code Review")
        or "<!-- REVIEWED_SHA:" in body
    )


def _has_suggestion_keywords(body):
    """判断是否包含明确的建议关键词（不含代码块这种弱信号）。"""
    indicators = [
        r"(?:建议|suggest|should|recommend|consider|最好|可以改|需要改|应该)",
        r"(?:改为|改成|换成|替换|replace|change.*to)",
        r"nit:",  # 常见 review 前缀
    ]
    for pattern in indicators:
        if re.search(pattern, body, re.IGNORECASE):
            return True
    return False


def _is_question(body):
    """判断是否是提问或设计质疑。"""
    indicators = [
        r"[?\uff1f]",  # 半角问号 ? 和全角问号 ？
        r"(?:为什么|why|how come|what if|是否|能否|有没有|wouldn't|shouldn't|isn't)",
        r"(?:这里为啥|这样做的原因|为何不|why not)",
    ]
    for pattern in indicators:
        if re.search(pattern, body, re.IGNORECASE):
            return True
    return False


def parse_datetime(dt_str):
    """解析时间字符串，始终返回 timezone-aware datetime。"""
    if not dt_str:
        return None
    dt_str = dt_str.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(dt_str)
    except ValueError:
        clean = re.sub(r"[+-]\d{2}:\d{2}$", "", dt_str)
        dt = datetime.strptime(clean, "%Y-%m-%dT%H:%M:%S")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def get_latest_commit_time(repo, token, pr_number):
    """获取 PR 最新 commit 的时间。"""
    owner, name = repo.split("/")
    try:
        commits = api_get(f"repos/{owner}/{name}/pulls/{pr_number}/commits", token)
        if commits:
            commit_obj = commits[-1].get("commit", {})
            date_str = (
                commit_obj.get("committer", {}).get("date")
                or commit_obj.get("author", {}).get("date")
            )
            return parse_datetime(date_str)
    except GitCodeError:
        pass
    return None


def parse_pr_comments(repo, pr_number, since_commit=False):
    """解析 PR 的所有评论，分类并提取 review 意见。

    返回：
    {
        "repo": "cann/hcomm",
        "pr": 584,
        "pr_author": "fan33",
        "total_comments": 25,
        "latest_commit_at": "2025-...",
        "review_comments": [
            {
                "type": "review_suggestion",
                "author": "reviewer_name",
                "body": "建议改成 ...",
                "created_at": "2025-...",
                "after_latest_push": true
            }
        ],
        "summary": {
            "bot_auto": 3,
            "bot_command": 8,
            "ci_result": 2,
            "review_suggestion": 5,
            "review_question": 2,
            "author_reply": 4,
            "other": 1
        }
    }
    """
    token = get_token()

    # 获取 PR 信息
    pr = get_pull(repo, token, pr_number)
    pr_author = pr.get("user", {}).get("login", "")

    # 获取最新 commit 时间
    latest_commit_at = get_latest_commit_time(repo, token, pr_number)

    # 获取所有评论
    comments = get_pull_comments(repo, token, pr_number)

    # 分类
    classified = []
    summary = {}
    for c in comments:
        ctype = classify_comment(c, pr_author)
        summary[ctype] = summary.get(ctype, 0) + 1

        comment_time = parse_datetime(c.get("created_at"))
        after_latest_push = False
        if latest_commit_at and comment_time:
            after_latest_push = comment_time > latest_commit_at

        classified.append({
            "id": c.get("id"),
            "type": ctype,
            "author": c.get("user", {}).get("login", ""),
            "body": c.get("body", ""),
            "file": c.get("path", ""),
            "line": c.get("position"),
            "created_at": c.get("created_at", ""),
            "after_latest_push": after_latest_push,
        })

    # 提取 review 相关评论
    review_types = {"review_suggestion", "review_question"}
    review_comments = [c for c in classified if c["type"] in review_types]

    # 如果 since_commit，只保留最新 push 之后的
    if since_commit:
        review_comments = [c for c in review_comments if c["after_latest_push"]]

    return {
        "repo": repo,
        "pr": pr_number,
        "pr_author": pr_author,
        "total_comments": len(comments),
        "latest_commit_at": latest_commit_at.isoformat() if latest_commit_at else None,
        "review_comments": review_comments,
        "summary": summary,
    }


def main():
    parser = argparse.ArgumentParser(description="解析 PR 评论，提取 review 意见")
    parser.add_argument("--repo", required=True, help="仓库，如 cann/hcomm")
    parser.add_argument("--pr", required=True, type=int, help="PR 编号")
    parser.add_argument(
        "--since-commit",
        action="store_true",
        help="只显示最新 push 之后的 review 评论",
    )
    args = parser.parse_args()

    try:
        result = parse_pr_comments(args.repo, args.pr, args.since_commit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except GitCodeError as e:
        print(f"错误：{e.message}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
