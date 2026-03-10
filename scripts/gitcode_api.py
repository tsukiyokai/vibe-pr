#!/usr/bin/env python3
"""GitCode API 封装层。

认证方式：从 ~/.git-credentials 读取 token，使用 PRIVATE-TOKEN header。
支持 GET/POST 请求、自动分页。
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path


GITCODE_BASE = "https://gitcode.com/api/v5"


class GitCodeError(Exception):
    """GitCode API 调用失败时抛出的异常。"""

    def __init__(self, message, status_code=None):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def get_token():
    """从 ~/.git-credentials 读取 gitcode.com 的 token。

    格式：https://username:token@gitcode.com
    """
    cred_path = Path.home() / ".git-credentials"
    if not cred_path.exists():
        raise GitCodeError("~/.git-credentials 不存在")

    text = cred_path.read_text()
    match = re.search(r"https://[^:]+:([^@]+)@gitcode\.com", text)
    if not match:
        raise GitCodeError("~/.git-credentials 中未找到 gitcode.com 的 token")

    return match.group(1)


def get_username():
    """从 ~/.git-credentials 读取 gitcode.com 的用户名。"""
    cred_path = Path.home() / ".git-credentials"
    if not cred_path.exists():
        raise GitCodeError("~/.git-credentials 不存在")
    text = cred_path.read_text()
    match = re.search(r"https://([^:]+):[^@]+@gitcode\.com", text)
    if not match:
        raise GitCodeError("~/.git-credentials 中未找到 gitcode.com 的用户名")
    return match.group(1)


def _request(method, url, token, data=None):
    """发送 HTTP 请求，返回解析后的 JSON。"""
    headers = {
        "PRIVATE-TOKEN": token,
        "Content-Type": "application/json",
    }

    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            if not body:
                return None
            return json.loads(body)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        hints = {
            401: "token 过期或无效",
            404: "资源不存在（检查仓库名和 PR 编号）",
            429: "API 限流，请稍后重试",
        }
        hint = hints.get(e.code, "")
        msg = f"HTTP {e.code}: {error_body}"
        if hint:
            msg += f" — {hint}"
        raise GitCodeError(msg, status_code=e.code)


def api_get(path, token, params=None):
    """GET 请求。path 为相对路径（如 repos/cann/hcomm/pulls）。"""
    url = f"{GITCODE_BASE}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return _request("GET", url, token)


def api_post(path, token, data=None):
    """POST 请求。"""
    url = f"{GITCODE_BASE}/{path}"
    return _request("POST", url, token, data)


def api_patch(path, token, data=None):
    """PATCH 请求。"""
    url = f"{GITCODE_BASE}/{path}"
    return _request("PATCH", url, token, data)


def api_get_paginated(path, token, params=None, max_pages=10):
    """分页 GET，自动合并所有页的结果。"""
    params = dict(params or {})
    params.setdefault("per_page", 100)
    params.setdefault("page", 1)

    all_results = []
    for _ in range(max_pages):
        results = api_get(path, token, params)
        if not results:
            break
        if not isinstance(results, list):
            return [results] if not all_results else all_results
        all_results.extend(results)
        if len(results) < params["per_page"]:
            break
        params["page"] += 1

    return all_results


# ── 高层 API ──────────────────────────────────────────────


def list_pulls(repo, token, state="open", **kwargs):
    """列出 PR。repo 格式：'cann/hcomm'。"""
    owner, name = repo.split("/")
    params = {"state": state, **kwargs}
    return api_get_paginated(f"repos/{owner}/{name}/pulls", token, params)


def get_pull(repo, token, number):
    """获取单个 PR 详情。"""
    owner, name = repo.split("/")
    return api_get(f"repos/{owner}/{name}/pulls/{number}", token)


def get_pull_comments(repo, token, number, **kwargs):
    """获取 PR 的所有评论。"""
    owner, name = repo.split("/")
    return api_get_paginated(
        f"repos/{owner}/{name}/pulls/{number}/comments", token, kwargs
    )


def post_comment(repo, token, number, body):
    """在 PR 上发布评论。"""
    owner, name = repo.split("/")
    return api_post(
        f"repos/{owner}/{name}/pulls/{number}/comments",
        token,
        {"body": body},
    )


def get_pull_labels(repo, token, number):
    """获取 PR 的标签列表。"""
    pr = get_pull(repo, token, number)
    return pr.get("labels", [])


def create_pull(repo, token, title, head, base, body=""):
    """创建 PR。head 格式：'username:branch'。"""
    owner, name = repo.split("/")
    return api_post(
        f"repos/{owner}/{name}/pulls",
        token,
        {"title": title, "head": head, "base": base, "body": body},
    )


def update_pull(repo, token, number, **fields):
    """更新 PR（标题、描述等）。fields 支持 title, body, state 等。"""
    owner, name = repo.split("/")
    return api_patch(f"repos/{owner}/{name}/pulls/{number}", token, fields)


def list_pull_files(repo, token, number):
    """获取 PR 的变更文件列表（含 patch diff）。

    返回列表，每项包含 filename, additions, deletions, patch 等字段。
    patch 为 dict 时，diff 文本在 patch["diff"] 中。
    """
    owner, name = repo.split("/")
    data = api_get(f"repos/{owner}/{name}/pulls/{number}/files", token)
    if data is None:
        return []
    if isinstance(data, list):
        return data
    return data.get("files", data.get("data", []))


def get_pull_diff(repo, token, number):
    """获取 PR 的合并 diff 文本。

    从 list_pull_files 的 patch 字段拼接完整 diff。
    返回 (diff_text, files) 元组。
    """
    files = list_pull_files(repo, token, number)
    parts = []
    for f in files:
        filename = f.get("filename", "")
        patch = f.get("patch", "")
        if isinstance(patch, dict):
            diff = patch.get("diff", "")
        else:
            diff = patch
        if diff:
            parts.append(f"--- a/{filename}\n+++ b/{filename}\n{diff}")
    return "\n".join(parts), files


def create_fork(repo, token):
    """Fork 仓库到当前用户。"""
    owner, name = repo.split("/")
    return api_post(f"repos/{owner}/{name}/forks", token)


if __name__ == "__main__":
    # 简单自测
    try:
        token = get_token()
        username = get_username()
        print(f"Token 读取成功，用户名：{username}")
        print(f"Token 前4位：{token[:4]}...")
    except GitCodeError as e:
        print(f"错误：{e.message}", file=sys.stderr)
        sys.exit(1)
