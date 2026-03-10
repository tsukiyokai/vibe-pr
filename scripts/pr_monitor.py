#!/usr/bin/env python3
"""PR Review 自动响应机器人。

轮询 PR 评论，自动修复 review_suggestion，通知 review_question。

用法：
    # 处理所有 review 评论，每 5 分钟轮询
    python3 pr_monitor.py --repo cann/hcomm --pr 584

    # 只处理人类 reviewer 评论
    python3 pr_monitor.py --repo cann/hcomm --pr 584 --human-only

    # 单次执行（调试用）
    python3 pr_monitor.py --repo cann/hcomm --pr 584 --once
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from comment_parser import parse_pr_comments
from gitcode_api import get_token, post_comment, GitCodeError
import review_tracker
import ci_log_fetcher

STATE_DIR = SCRIPTS_DIR.parent / "state"


# ── 日志 ──────────────────────────────────────────────────


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ── State 管理 ────────────────────────────────────────────


def _state_path(repo, pr):
    return STATE_DIR / f"{repo.replace('/', '_')}_{pr}.json"


def load_state(repo, pr):
    path = _state_path(repo, pr)
    if path.exists():
        return json.loads(path.read_text())
    return {"processed_ids": [], "fix_rounds": 0}


def save_state(repo, pr, state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _state_path(repo, pr).write_text(
        json.dumps(state, ensure_ascii=False, indent=2)
    )


# ── Git 操作 ──────────────────────────────────────────────


def check_git_clean(work_dir):
    r = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=work_dir, capture_output=True, text=True,
    )
    return not r.stdout.strip()


def has_git_changes(work_dir):
    r = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=work_dir, capture_output=True, text=True,
    )
    return bool(r.stdout.strip())


def commit_and_push(work_dir):
    try:
        subprocess.run(["git", "add", "-A"], cwd=work_dir, check=True)
        subprocess.run(
            ["git", "commit", "-m", "fix: address review comments"],
            cwd=work_dir, check=True,
        )
        r = subprocess.run(
            ["git", "push"], cwd=work_dir,
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            log(f"push 失败: {r.stderr[:300]}")
            return False
        log("commit + push 成功")
        return True
    except subprocess.CalledProcessError as e:
        log(f"git 操作失败: {e}")
        return False


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


def is_already_handled(repo, pr, comment_id):
    """Check if a comment is already fixed or escalated in review_tracker."""
    tracker = review_tracker._load(repo, pr)
    for c in tracker["comments"]:
        if c["id"] == comment_id and c["status"] in ("fixed", "needs_user", "rejected"):
            return True
    return False


# ── Adaptive Interval ────────────────────────────────────


def adaptive_interval(base_interval, last_activity_age_seconds):
    """Adjust polling interval based on recent activity."""
    if last_activity_age_seconds < 300:
        return max(60, base_interval // 3)
    elif last_activity_age_seconds > 3600:
        return min(600, base_interval * 2)
    return base_interval


# ── CI 触发 ───────────────────────────────────────────────


def trigger_ci(repo, pr):
    try:
        post_comment(repo, get_token(), pr, "compile")
        log("已触发 CI（compile）")
    except GitCodeError as e:
        log(f"触发 CI 失败: {e.message}")


# ── 评论轮询 ──────────────────────────────────────────────


def get_new_comments(repo, pr, state, human_only, extra_bots):
    """Pull new comments, classify, sync to tracker. Returns (suggestions, questions)."""
    data = parse_pr_comments(repo, pr, since_commit=True)
    processed = set(state["processed_ids"])

    # Sync to review tracker, reusing already-fetched data to avoid duplicate API call
    review_tracker.sync(repo, pr, parsed_data=data)

    suggestions, questions = [], []
    for c in data["review_comments"]:
        cid = c.get("id")
        if cid is not None and cid in processed:
            continue
        if human_only and extra_bots:
            if c["author"].lower() in {b.lower() for b in extra_bots}:
                continue

        # Skip comments already handled in review_tracker
        if is_already_handled(repo, pr, cid):
            continue

        if c["type"] == "review_suggestion":
            suggestions.append(c)
        elif c["type"] == "review_question":
            questions.append(c)

    return suggestions, questions


def mark_processed(state, comments):
    """将评论 ID 加入 processed_ids。"""
    for c in comments:
        cid = c.get("id")
        if cid is not None and cid not in state["processed_ids"]:
            state["processed_ids"].append(cid)


# ── 主循环 ────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser(description="PR Review 自动响应机器人")
    ap.add_argument("--repo", required=True, help="仓库 (如 cann/hcomm)")
    ap.add_argument("--pr", required=True, type=int, help="PR 编号")
    ap.add_argument("--interval", type=int, default=300,
                    help="轮询间隔秒数 (默认 300)")
    ap.add_argument("--max-rounds", type=int, default=5,
                    help="最大修复轮次 (默认 5)")
    ap.add_argument("--human-only", action="store_true",
                    help="只处理人类评论")
    ap.add_argument("--bot-accounts", default="",
                    help="额外 bot 账号 (逗号分隔)")
    ap.add_argument("--work-dir", default=".",
                    help="仓库目录 (默认当前目录)")
    ap.add_argument("--once", action="store_true",
                    help="只执行一次，不循环")
    ap.add_argument("--adaptive", action="store_true",
                    help="Adapt polling interval based on activity")
    args = ap.parse_args()

    work_dir = os.path.abspath(args.work_dir)
    extra_bots = {b.strip() for b in args.bot_accounts.split(",") if b.strip()}

    # 前置检查
    if not os.path.isdir(os.path.join(work_dir, ".git")):
        print(f"错误：{work_dir} 不是 git 仓库", file=sys.stderr)
        sys.exit(1)
    if not check_git_clean(work_dir):
        print("错误：工作目录有未提交的改动，请先 commit 或 stash",
              file=sys.stderr)
        sys.exit(1)

    # 信号处理
    running = [True]

    def on_signal(sig, frame):
        log("收到退出信号，停止中...")
        running[0] = False

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    state = load_state(args.repo, args.pr)
    log(f"监控启动：{args.repo} !{args.pr}")
    log(f"轮次 {state['fix_rounds']}/{args.max_rounds}，"
        f"已处理 {len(state['processed_ids'])} 条评论")

    while running[0]:
        # 轮次上限
        if state["fix_rounds"] >= args.max_rounds:
            log(f"已达最大轮次 ({args.max_rounds})，停止")
            break

        # 拉取评论
        try:
            suggestions, questions = get_new_comments(
                args.repo, args.pr, state,
                args.human_only, extra_bots,
            )
        except GitCodeError as e:
            log(f"API 错误: {e.message}")
            if args.once:
                break
            time.sleep(min(args.interval, 60))
            continue

        # 通知 review_question（不自动改）
        for q in questions:
            preview = q["body"][:120].replace("\n", " ")
            log(f"[问题] {q['author']}: {preview}")
        mark_processed(state, questions)

        # 处理 review_suggestion
        if suggestions:
            log(f"Found {len(suggestions)} suggestions")
            for s in suggestions:
                preview = s["body"][:120].replace("\n", " ")
                log(f"  - {s['author']}: {preview}")

            task = generate_review_task(suggestions, args.repo, args.pr, work_dir)
            print(json.dumps(task, ensure_ascii=False, indent=2), flush=True)

            mark_processed(state, suggestions)
            state["fix_rounds"] += 1
            log(f"Round {state['fix_rounds']}/{args.max_rounds} task generated")
        else:
            if not questions:
                log("No new comments")

        # 持久化
        if suggestions or questions:
            save_state(args.repo, args.pr, state)

        if args.once:
            break

        # Adaptive or fixed interval
        interval = args.interval
        if args.adaptive:
            try:
                data = parse_pr_comments(args.repo, args.pr, since_commit=False)
                if data.get("review_comments"):
                    last = data["review_comments"][-1].get("created_at", "")
                    from comment_parser import parse_datetime
                    from datetime import timezone
                    age = (datetime.now(timezone.utc) - parse_datetime(last)).total_seconds()
                    interval = adaptive_interval(args.interval, age)
            except Exception:
                pass

        log(f"Waiting {interval}s...")
        for _ in range(interval):
            if not running[0]:
                break
            time.sleep(1)

    save_state(args.repo, args.pr, state)
    log("监控结束")


if __name__ == "__main__":
    main()
