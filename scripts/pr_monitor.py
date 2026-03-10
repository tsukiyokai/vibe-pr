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


# ── Claude 修复 ───────────────────────────────────────────


def build_prompt(suggestions, repo, pr):
    lines = [
        f"你是 CANN 社区代码修复助手。仓库 {repo} 的 PR !{pr} 收到了以下 review 意见。",
        "请搜索相关代码并逐条修复。",
        "",
    ]
    for i, s in enumerate(suggestions, 1):
        lines.append(f"## 意见 {i}（{s['author']}）")
        lines.append("")
        lines.append(s["body"])
        lines.append("")
    lines.extend([
        "## 规则",
        "1. 只改 reviewer 指出的问题，不做额外改动",
        "2. 保持代码风格一致",
        "3. 意见不明确或涉及设计决策时，跳过并说明原因",
    ])
    return "\n".join(lines)


def run_claude_fix(prompt, work_dir):
    log("调用 claude -p 修复...")
    try:
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        r = subprocess.run(
            ["claude", "-p",
             "--allowedTools", "Read,Edit,Glob,Grep,Bash(git diff:*),Bash(git status:*)"],
            input=prompt,
            cwd=work_dir,
            capture_output=True, text=True,
            timeout=600,
            env=env,
        )
        if r.stdout:
            log("claude 输出：")
            out = r.stdout
            if len(out) > 3000:
                out = out[:3000] + "\n... (truncated)"
            print(out, flush=True)
        if r.returncode != 0:
            log(f"claude -p 返回码 {r.returncode}")
            if r.stderr:
                log(f"stderr: {r.stderr[:500]}")
            return False
        return True
    except subprocess.TimeoutExpired:
        log("claude -p 超时（10分钟）")
        return False
    except FileNotFoundError:
        log("错误：未找到 claude 命令，请确认已安装 Claude CLI")
        return False


# ── CI 触发 ───────────────────────────────────────────────


def trigger_ci(repo, pr):
    try:
        post_comment(repo, get_token(), pr, "compile")
        log("已触发 CI（compile）")
    except GitCodeError as e:
        log(f"触发 CI 失败: {e.message}")


# ── 评论轮询 ──────────────────────────────────────────────


def get_new_comments(repo, pr, state, human_only, extra_bots):
    """拉取新评论，按类型分拣。返回 (suggestions, questions)。"""
    data = parse_pr_comments(repo, pr, since_commit=True)
    processed = set(state["processed_ids"])

    suggestions, questions = [], []
    for c in data["review_comments"]:
        cid = c.get("id")
        if cid is not None and cid in processed:
            continue
        if human_only and extra_bots:
            if c["author"].lower() in {b.lower() for b in extra_bots}:
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
            log(f"发现 {len(suggestions)} 条修改建议")
            for s in suggestions:
                preview = s["body"][:120].replace("\n", " ")
                log(f"  - {s['author']}: {preview}")

            prompt = build_prompt(suggestions, args.repo, args.pr)
            ok = run_claude_fix(prompt, work_dir)

            if ok and has_git_changes(work_dir):
                if commit_and_push(work_dir):
                    trigger_ci(args.repo, args.pr)
                    state["fix_rounds"] += 1
                    log(f"轮次 {state['fix_rounds']}/{args.max_rounds} 完成")
                else:
                    log("push 失败，跳过本轮")
            elif ok:
                log("无文件变更（意见可能已处理或无需修改）")
            else:
                log("claude 修复失败，跳过本轮")

            mark_processed(state, suggestions)
        else:
            if not questions:
                log("无新评论")

        # 持久化
        if suggestions or questions:
            save_state(args.repo, args.pr, state)

        if args.once:
            break

        # 可中断的 sleep
        log(f"等待 {args.interval}s...")
        for _ in range(args.interval):
            if not running[0]:
                break
            time.sleep(1)

    save_state(args.repo, args.pr, state)
    log("监控结束")


if __name__ == "__main__":
    main()
