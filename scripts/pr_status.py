#!/usr/bin/env python3
"""查询 CANN 社区 PR 的当前状态。

输出结构化 JSON，包含 CLA、CI、lgtm/approve 进度、模块审批详情。

用法：
    python pr_status.py --repo cann/hcomm --pr 584
"""

import argparse
import json
import re
import sys

from gitcode_api import get_token, get_pull, get_pull_comments, api_get, GitCodeError


def parse_bot_welcome(comments):
    """从 cann-robot 的欢迎评论中解析模块审批表格。

    返回模块列表，每个模块包含：
    - name: 模块名
    - lgtm: {need, got, candidates}
    - approve: {need, got, candidates}
    """
    modules = []

    # 从后往前遍历，取最新的 bot 欢迎评论（PR 多次变更后 bot 会重新发表格）
    for c in reversed(comments):
        body = c.get("body", "")
        if "| module |" not in body.lower() and "| module" not in body:
            continue

        # 解析表格行，格式示例：
        # | src | ❌ (0/2)(You can also ask: a, b) | ❌ (0/1)(You can also ask: c, d) |
        # 或者已通过：
        # | src | ✅ (2/2) | ✅ (1/1) |
        for line in body.split("\n"):
            line = line.strip()
            if not line.startswith("|"):
                continue
            cells = [cell.strip() for cell in line.split("|")]
            cells = [cell for cell in cells if cell]

            if len(cells) < 3:
                continue
            if cells[0].lower() in ("module", "---", "----", "-----"):
                continue
            if cells[0].startswith("-"):
                continue

            module_name = cells[0]
            lgtm_cell = cells[1] if len(cells) > 1 else ""
            approve_cell = cells[2] if len(cells) > 2 else ""

            modules.append({
                "name": module_name,
                "lgtm": _parse_status_cell(lgtm_cell),
                "approve": _parse_status_cell(approve_cell),
            })

        if modules:
            break  # 取最新的即可

    return modules


def _parse_status_cell(cell):
    """解析单个状态单元格，如 '❌ (0/2)(You can also ask: a, b, c)'。"""
    result = {"need": 0, "got": 0, "candidates": []}

    # 匹配 (got/need)
    ratio_match = re.search(r"\((\d+)/(\d+)\)", cell)
    if ratio_match:
        result["got"] = int(ratio_match.group(1))
        result["need"] = int(ratio_match.group(2))

    # 匹配候选人列表
    # 格式：[*username*](https://gitcode.com/username)
    candidates_match = re.search(r"You can also ask:\s*(.+)", cell)
    if candidates_match:
        raw = candidates_match.group(1)
        # 提取 markdown 链接中的用户名：[*name*](url)
        names = re.findall(r"\[\*(\w+)\*\]", raw)
        if not names:
            # 回退：按逗号分割纯文本
            names = [n.strip().strip("*)[]") for n in raw.split(",") if n.strip()]
        result["candidates"] = names

    return result


def get_pr_status(repo, pr_number):
    """获取 PR 的完整状态。"""
    token = get_token()

    # 获取 PR 详情（含标签）
    pr = get_pull(repo, token, pr_number)
    labels = [lb["name"] for lb in pr.get("labels", [])]

    # CLA 状态
    cla = "cann-cla/yes" in labels

    # CI 状态
    if "ci-pipeline-passed" in labels:
        ci = "passed"
    elif "ci-pipeline-running" in labels:
        ci = "running"
    elif "ci-pipeline-failed" in labels:
        ci = "failed"
    else:
        ci = "not_started"

    # 获取最新 commit 时间
    owner, name = repo.split("/")
    latest_commit_at = None
    try:
        commits = api_get(f"repos/{owner}/{name}/pulls/{pr_number}/commits", token)
        if commits:
            commit_obj = commits[-1].get("commit", {})
            latest_commit_at = (
                commit_obj.get("committer", {}).get("date")
                or commit_obj.get("author", {}).get("date")
            )
    except GitCodeError:
        pass  # 获取失败时降级，不影响其他字段

    # 获取评论，解析模块审批
    comments = get_pull_comments(repo, token, pr_number)
    modules = parse_bot_welcome(comments)

    # 汇总 lgtm/approve 进度
    total_lgtm_need = sum(m["lgtm"]["need"] for m in modules)
    total_lgtm_got = sum(m["lgtm"]["got"] for m in modules)
    total_approve_need = sum(m["approve"]["need"] for m in modules)
    total_approve_got = sum(m["approve"]["got"] for m in modules)

    # 全局标签状态
    has_lgtm_label = "lgtm" in labels
    has_approved_label = "approved" in labels

    # 合并就绪检查
    merge_ready = all([cla, ci == "passed", has_lgtm_label, has_approved_label])

    return {
        "repo": repo,
        "pr": pr_number,
        "title": pr.get("title", ""),
        "state": pr.get("state", ""),
        "latest_commit_at": latest_commit_at,
        "cla": cla,
        "ci": ci,
        "lgtm": {
            "need": total_lgtm_need,
            "got": total_lgtm_got,
            "label": has_lgtm_label,
        },
        "approve": {
            "need": total_approve_need,
            "got": total_approve_got,
            "label": has_approved_label,
        },
        "modules": modules,
        "merge_ready": merge_ready,
        "labels": labels,
    }


def main():
    parser = argparse.ArgumentParser(description="查询 CANN PR 状态")
    parser.add_argument("--repo", required=True, help="仓库，如 cann/hcomm")
    parser.add_argument("--pr", required=True, type=int, help="PR 编号")
    args = parser.parse_args()

    status = get_pr_status(args.repo, args.pr)
    print(json.dumps(status, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
