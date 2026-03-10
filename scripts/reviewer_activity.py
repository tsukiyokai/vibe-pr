#!/usr/bin/env python3
"""分析 CANN 社区 reviewer 候选人的活跃度。

从最近已合并的 PR 中统计每个候选人的 /lgtm 和 /approve 次数、响应时间。

用法：
    # 自动从指定 PR 的 bot 欢迎评论中解析候选人
    python reviewer_activity.py --repo cann/hcomm --pr 584 --recent 30

    # 手动指定候选人列表
    python reviewer_activity.py --repo cann/hcomm --candidates "yanyefeng,lilin_137" --recent 30
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone

from gitcode_api import get_token, list_pulls, get_pull_comments, GitCodeError
from pr_status import parse_bot_welcome


def parse_datetime(dt_str):
    """解析 GitCode API 返回的时间字符串。"""
    # 格式如 "2025-01-15T10:30:00+08:00" 或 "2025-01-15T02:30:00Z"
    dt_str = dt_str.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(dt_str)
    except ValueError:
        # 回退：去掉时区信息
        clean = re.sub(r"[+-]\d{2}:\d{2}$", "", dt_str)
        return datetime.strptime(clean, "%Y-%m-%dT%H:%M:%S")


def get_candidates_from_pr(repo, token, pr_number):
    """从 PR 的 bot 欢迎评论中提取所有候选 reviewer。"""
    comments = get_pull_comments(repo, token, pr_number)
    modules = parse_bot_welcome(comments)

    candidates = set()
    for m in modules:
        candidates.update(m["lgtm"].get("candidates", []))
        candidates.update(m["approve"].get("candidates", []))

    return list(candidates)


def analyze_activity(repo, token, candidates, recent_count=30):
    """分析候选人在最近已合并 PR 中的活跃度。

    返回按活跃度排序的列表。
    """
    # 获取最近已合并的 PR
    # GitCode API: state=merged 直接返回已合并 PR
    merged_prs = list_pulls(repo, token, state="merged", per_page=recent_count)
    merged_prs = merged_prs[:recent_count]

    if not merged_prs:
        print(f"警告：未找到已合并的 PR", file=sys.stderr)
        return []

    # 初始化统计
    stats = {}
    for c in candidates:
        stats[c] = {
            "name": c,
            "lgtm_count": 0,
            "approve_count": 0,
            "reviews": 0,
            "response_times_hours": [],
        }

    # 遍历每个已合并 PR 的评论
    failed_prs = []
    for i, pr in enumerate(merged_prs, 1):
        pr_number = pr["number"]
        pr_created = parse_datetime(pr["created_at"])

        print(f"[{i}/{len(merged_prs)}] 分析 PR #{pr_number}...", file=sys.stderr)

        try:
            comments = get_pull_comments(repo, token, pr_number)
        except GitCodeError as e:
            print(f"  跳过 PR #{pr_number}：{e.message}", file=sys.stderr)
            failed_prs.append(pr_number)
            continue

        for comment in comments:
            author = comment.get("user", {}).get("login", "")
            if author not in stats:
                continue

            body = comment.get("body", "").strip()
            comment_time = parse_datetime(comment["created_at"])

            is_lgtm = body == "/lgtm" or body.startswith("/lgtm\n")
            is_approve = body == "/approve" or body.startswith("/approve\n")

            if is_lgtm:
                stats[author]["lgtm_count"] += 1
                stats[author]["reviews"] += 1
                delta = (comment_time - pr_created).total_seconds() / 3600
                if delta > 0:
                    stats[author]["response_times_hours"].append(delta)

            if is_approve:
                stats[author]["approve_count"] += 1
                stats[author]["reviews"] += 1
                delta = (comment_time - pr_created).total_seconds() / 3600
                if delta > 0:
                    stats[author]["response_times_hours"].append(delta)

    # 汇报进度
    success_count = len(merged_prs) - len(failed_prs)
    print(f"分析完成：成功 {success_count} 个 PR，失败 {len(failed_prs)} 个", file=sys.stderr)
    if failed_prs:
        print(f"失败的 PR：{', '.join(f'#{n}' for n in failed_prs)}", file=sys.stderr)

    # 计算响应率和平均响应时间
    result = []
    for name, s in stats.items():
        times = s["response_times_hours"]
        within_24h = sum(1 for t in times if t <= 24)

        entry = {
            "name": name,
            "reviews": s["reviews"],
            "lgtm_count": s["lgtm_count"],
            "approve_count": s["approve_count"],
            "response_rate_24h": round(within_24h / len(times), 2) if times else 0,
            "avg_response_hours": round(sum(times) / len(times), 1) if times else None,
            "prs_analyzed": len(merged_prs),
        }
        result.append(entry)

    # 按 reviews 数降序排序
    result.sort(key=lambda x: x["reviews"], reverse=True)
    return result


def main():
    parser = argparse.ArgumentParser(description="分析 CANN reviewer 活跃度")
    parser.add_argument("--repo", required=True, help="仓库，如 cann/hcomm")
    parser.add_argument("--pr", type=int, help="从此 PR 的 bot 评论中解析候选人")
    parser.add_argument("--candidates", help="逗号分隔的候选人列表")
    parser.add_argument("--recent", type=int, default=30, help="分析最近 N 个已合并 PR（默认 30）")
    args = parser.parse_args()

    try:
        token = get_token()
    except GitCodeError as e:
        print(f"错误：{e.message}", file=sys.stderr)
        sys.exit(1)

    # 确定候选人列表
    if args.candidates:
        candidates = [c.strip() for c in args.candidates.split(",") if c.strip()]
    elif args.pr:
        try:
            candidates = get_candidates_from_pr(args.repo, token, args.pr)
        except GitCodeError as e:
            print(f"错误：获取 PR #{args.pr} 失败：{e.message}", file=sys.stderr)
            sys.exit(1)
        if not candidates:
            print("错误：未能从 PR 评论中解析出候选人", file=sys.stderr)
            sys.exit(1)
        print(f"从 PR #{args.pr} 解析出候选人：{', '.join(candidates)}", file=sys.stderr)
    else:
        print("错误：必须指定 --pr 或 --candidates", file=sys.stderr)
        sys.exit(1)

    result = analyze_activity(args.repo, token, candidates, args.recent)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
