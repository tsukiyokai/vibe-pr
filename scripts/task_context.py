#!/usr/bin/env python3
"""管理 PR 的跨 session 工作上下文。

三种模式：
    --init：从 PR 当前状态生成初始 context 文件
    --repo X --pr N：读取并打印已有 context 文件
    --list：列出所有活跃 PR 的 context 摘要

context 文件存放在 ~/.claude/skills/vibe-pr/context/ 下，
供 Claude 在新 session 中恢复工作上下文。
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

CONTEXT_DIR = os.path.expanduser("~/.claude/skills/vibe-pr/context")

# 确保 scripts 目录在 path 中，以便 import pr_status
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def context_path(repo, pr):
    """生成 context 文件路径：cann_hcomm_584.md"""
    safe_name = repo.replace("/", "_")
    return os.path.join(CONTEXT_DIR, f"{safe_name}_{pr}.md")


def infer_phase(status):
    """从 PR 标签推断当前阶段（best-effort）。"""
    if status["state"] == "merged":
        return 6, "已合并"
    if status["merge_ready"]:
        return 6, "四标签就位，等待合并"
    if status["lgtm"]["got"] > 0 or status["approve"]["got"] > 0:
        return 5.5, "收到 review 意见"
    if status["ci"] == "passed":
        return 5, "CI 通过，找 Reviewer"
    if status["ci"] == "failed":
        return 4, "CI 失败，需修复后重新触发"
    if status["ci"] == "running":
        return 4, "CI 运行中"
    if status["cla"]:
        return 4, "CLA 通过，待触发 CI"
    return 3, "PR 已创建，待检查 CLA"


def build_progress(phase, status):
    """根据推断的阶段生成进度 checklist。"""
    lines = []

    # 阶段 2：PR 已创建（能运行到这里说明 PR 存在）
    lines.append("- [x] 阶段 2：PR 已创建")

    # 阶段 3：CLA
    if status["cla"]:
        lines.append("- [x] 阶段 3：CLA 通过")
    else:
        lines.append("- [ ] 阶段 3：CLA 未通过")

    # 阶段 4：CI
    if status["ci"] == "passed":
        lines.append("- [x] 阶段 4：CI 通过")
    elif status["ci"] == "running":
        lines.append("- [ ] 阶段 4：CI 运行中")
    elif status["ci"] == "failed":
        lines.append("- [ ] 阶段 4：CI 失败")
    else:
        lines.append("- [ ] 阶段 4：CI 未触发")

    # 阶段 5：Review
    lgtm_str = f'{status["lgtm"]["got"]}/{status["lgtm"]["need"]}'
    approve_str = f'{status["approve"]["got"]}/{status["approve"]["need"]}'
    if status["lgtm"]["label"] and status["approve"]["label"]:
        lines.append(f"- [x] 阶段 5：Review 完成（lgtm {lgtm_str}, approve {approve_str}）")
    elif status["lgtm"]["got"] > 0 or status["approve"]["got"] > 0:
        lines.append(f"- [ ] 阶段 5：Review 进行中（lgtm {lgtm_str}, approve {approve_str}）")
    else:
        lines.append(f"- [ ] 阶段 5：等待 Review（lgtm {lgtm_str}, approve {approve_str}）")

    # 阶段 6：合并
    if status["state"] == "merged":
        lines.append("- [x] 阶段 6：已合并")
    elif status["merge_ready"]:
        lines.append("- [ ] 阶段 6：等待自动合并")
    else:
        lines.append("- [ ] 阶段 6：未就绪")

    return "\n".join(lines)


def build_todos(phase, status):
    """根据当前阶段生成初始待办。"""
    todos = []
    if not status["cla"]:
        todos.append("- 检查 CLA 签署状态，确认 committer email 正确")
    if status["ci"] == "not_started":
        todos.append("- 在 PR 评论区发送 `compile` 触发 CI")
    elif status["ci"] == "failed":
        todos.append("- 用 ci_log_fetcher.py 查看失败原因，修复后重新触发 CI")
    elif status["ci"] == "running":
        todos.append("- 等待 CI 完成，用 pr_status.py 检查结果")
    if phase < 5 and status["ci"] == "passed":
        todos.append("- 分析 reviewer 候选人，向用户推荐")
    if phase >= 5 and not status["merge_ready"]:
        if not status["lgtm"]["label"] or not status["approve"]["label"]:
            todos.append("- 跟进 review 进度")
    if status["merge_ready"] and status["state"] != "merged":
        todos.append("- 等待 cann-robot 自动 squash merge")
    return "\n".join(todos) if todos else "（无）"


def generate_context(status):
    """生成 context 文件内容。"""
    phase, phase_desc = infer_phase(status)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    progress = build_progress(phase, status)
    todos = build_todos(phase, status)

    # head_label: 从 PR 详情获取（如果有的话）
    head_label = status.get("head_label", "")

    return f"""# PR !{status["pr"]} — {status["title"]}

repo: {status["repo"]}
pr: {status["pr"]}
branch: {head_label}
updated: {now}

## 当前阶段

阶段 {phase}（{phase_desc}）

## 进度

{progress}

## 关键决策

（由 Claude 在后续交互中填写）

## 待处理

{todos}
"""


def cmd_init(repo, pr):
    """初始化 context 文件。已存在则打印现有内容。"""
    path = context_path(repo, pr)

    if os.path.exists(path):
        print(f"context 文件已存在：{path}")
        print()
        with open(path) as f:
            print(f.read())
        return

    # 从 API 获取 PR 状态
    from pr_status import get_pr_status

    status = get_pr_status(repo, pr)

    # 尝试获取 head_label
    from gitcode_api import get_token, get_pull
    try:
        pr_detail = get_pull(repo, get_token(), pr)
        status["head_label"] = pr_detail.get("head", {}).get("label", "")
    except Exception:
        status["head_label"] = ""

    content = generate_context(status)

    os.makedirs(CONTEXT_DIR, exist_ok=True)
    with open(path, "w") as f:
        f.write(content)

    print(f"已创建 context 文件：{path}")
    print()
    print(content)


def cmd_read(repo, pr):
    """读取并打印 context 文件。"""
    path = context_path(repo, pr)

    if not os.path.exists(path):
        print(f"无 context 文件：{path}")
        print(f"用 --init 创建：python3 {__file__} --repo {repo} --pr {pr} --init")
        sys.exit(1)

    with open(path) as f:
        print(f.read())


def cmd_list():
    """列出所有活跃 context 的摘要。"""
    if not os.path.exists(CONTEXT_DIR):
        print("[]")
        return

    summaries = []
    for fname in sorted(os.listdir(CONTEXT_DIR)):
        if not fname.endswith(".md"):
            continue

        path = os.path.join(CONTEXT_DIR, fname)
        with open(path) as f:
            content = f.read()

        # 解析元数据
        summary = {"file": fname}

        repo_match = re.search(r"^repo:\s*(.+)$", content, re.MULTILINE)
        if repo_match:
            summary["repo"] = repo_match.group(1).strip()

        pr_match = re.search(r"^pr:\s*(\d+)", content, re.MULTILINE)
        if pr_match:
            summary["pr"] = int(pr_match.group(1))

        updated_match = re.search(r"^updated:\s*(.+)$", content, re.MULTILINE)
        if updated_match:
            summary["updated"] = updated_match.group(1).strip()

        phase_match = re.search(r"^阶段\s+(\S+)（(.+?)）", content, re.MULTILINE)
        if phase_match:
            summary["phase"] = phase_match.group(1)
            summary["phase_desc"] = phase_match.group(2)

        summaries.append(summary)

    print(json.dumps(summaries, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="管理 PR 跨 session 工作上下文")
    parser.add_argument("--repo", help="仓库，如 cann/hcomm")
    parser.add_argument("--pr", type=int, help="PR 编号")
    parser.add_argument("--init", action="store_true", help="初始化 context 文件")
    parser.add_argument("--list", action="store_true", dest="list_all", help="列出所有活跃 context")
    args = parser.parse_args()

    if args.list_all:
        cmd_list()
    elif args.repo and args.pr is not None:
        if args.init:
            cmd_init(args.repo, args.pr)
        else:
            cmd_read(args.repo, args.pr)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
