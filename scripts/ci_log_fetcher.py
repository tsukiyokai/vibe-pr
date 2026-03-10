#!/usr/bin/env python3
"""CI log fetcher for CANN PRs. Extracts CI results from PR comments or CodeArts API.

CodeArts API 链路：
1. 从 cann-robot 评论的 log_url 解析 pipelineId + pipelineRunId
2. GET  /v6/{domain_id}/api/pac/pipelines/actions/{pipelineId}/{pipelineRunId}
   → 获取 jobs 列表（含 job id, name, status）
3. POST /v6/{domain_id}/api/pac/pipelines/actions/{pipelineId}/{pipelineRunId}/{jobRunId}/logs
   → 获取实际日志内容
"""
import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import urllib.error
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


# ── CodeArts API ─────────────────────────────────────────

# openlibing.com 上 CodeArts 流水线的 API 基地址
CODEARTS_BASE = os.environ.get(
    "CODEARTS_BASE", "https://www.openlibing.com"
)
# domain_id 通过环境变量配置（华为云租户 ID）
CODEARTS_DOMAIN_ID = os.environ.get("CODEARTS_DOMAIN_ID", "")


def parse_pipeline_url(url: str) -> dict:
    """从 CodeArts 流水线 URL 提取 pipelineId 和 pipelineRunId。

    URL 格式：
        https://www.openlibing.com/apps/pipelineDetail?pipelineId=XXX&pipelineRunId=YYY&projectName=CANN
    """
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    result = {}
    if "pipelineId" in params:
        result["pipeline_id"] = params["pipelineId"][0]
    if "pipelineRunId" in params:
        result["pipeline_run_id"] = params["pipelineRunId"][0]
    if "projectName" in params:
        result["project_name"] = params["projectName"][0]
    return result


def _codearts_request(method, url, token, data=None):
    """发送 CodeArts API 请求。

    尝试两种认证方式：
    1. GitCode PRIVATE-TOKEN（openlibing.com 可能代理到 CodeArts）
    2. X-Auth-Token（华为云标准认证）
    """
    headers = {
        "Content-Type": "application/json",
        "PRIVATE-TOKEN": token,
        "X-Auth-Token": token,
    }
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            if not raw.strip():
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")[:500]
        raise CodeArtsAPIError(
            f"HTTP {e.code}: {error_body}", status_code=e.code
        )
    except (urllib.error.URLError, OSError) as e:
        raise CodeArtsAPIError(f"网络错误: {e}")


class CodeArtsAPIError(Exception):
    """CodeArts API 调用失败。"""
    def __init__(self, message, status_code=None):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def get_pipeline_run_detail(token, domain_id, pipeline_id, pipeline_run_id):
    """获取流水线运行详情（含 jobs 列表）。

    GET /v6/{domain_id}/api/pac/pipelines/actions/{pipeline_id}/{pipeline_run_id}
    """
    url = (
        f"{CODEARTS_BASE}/v6/{domain_id}/api/pac/pipelines/actions"
        f"/{pipeline_id}/{pipeline_run_id}"
    )
    return _codearts_request("GET", url, token)


def get_pipeline_job_log(token, domain_id, pipeline_id, pipeline_run_id,
                         job_run_id, offset=0, limit=500):
    """获取流水线 job 日志。

    POST /v6/{domain_id}/api/pac/pipelines/actions/{pipeline_id}/{pipeline_run_id}/{job_run_id}/logs
    """
    url = (
        f"{CODEARTS_BASE}/v6/{domain_id}/api/pac/pipelines/actions"
        f"/{pipeline_id}/{pipeline_run_id}/{job_run_id}/logs"
    )
    return _codearts_request("POST", url, token, {
        "offset": offset,
        "limit": limit,
    })


def fetch_full_job_log(token, domain_id, pipeline_id, pipeline_run_id,
                       job_run_id, max_pages=20):
    """分页拉取完整的 job 日志。"""
    all_log = []
    offset = 0
    for _ in range(max_pages):
        result = get_pipeline_job_log(
            token, domain_id, pipeline_id, pipeline_run_id,
            job_run_id, offset=offset,
        )
        log_text = result.get("log", "")
        if log_text:
            all_log.append(log_text)
        if not result.get("has_more", False):
            break
        offset = result.get("end_offset", offset + 500)
    return "\n".join(all_log)


def fetch_from_api(repo: str, pr: int, task_filter: str = "all") -> dict:
    """从 CodeArts API 获取 CI 日志。

    流程：
    1. 从 PR 评论中提取流水线 URL → 解析出 pipelineId/pipelineRunId
    2. 调 CodeArts API 获取 jobs 列表
    3. 对失败的 job 拉取完整日志
    """
    domain_id = CODEARTS_DOMAIN_ID
    if not domain_id:
        raise NotImplementedError(
            "需要设置 CODEARTS_DOMAIN_ID 环境变量（华为云租户 ID）。"
            "可通过 export CODEARTS_DOMAIN_ID=xxx 设置。"
        )

    token = gitcode_api.get_token()

    # Step 1: 从评论获取流水线 URL
    comment_result = fetch_from_comments(repo, pr, task_filter="all")
    if not comment_result["tasks"]:
        return {
            "source": "api",
            "repo": repo,
            "pr": pr,
            "tasks": [],
            "error": "PR 评论中未找到 CI 结果",
        }

    # 为每个 task 关联其 pipeline URL 信息
    task_pipelines = {}
    for i, task in enumerate(comment_result["tasks"]):
        url = task.get("log_url", "")
        if url:
            parsed = parse_pipeline_url(url)
            pid = parsed.get("pipeline_id")
            prid = parsed.get("pipeline_run_id")
            if pid and prid:
                task_pipelines[i] = (pid, prid)

    if not task_pipelines:
        return {
            "source": "api",
            "repo": repo,
            "pr": pr,
            "tasks": comment_result["tasks"],
            "error": "评论中未找到有效的 CodeArts 流水线 URL",
        }

    # Step 2: 获取流水线运行详情（缓存，避免重复请求）
    pipeline_details = {}
    tasks_with_logs = []

    for i, task in enumerate(comment_result["tasks"]):
        task_name = task["name"].lower()
        if task_filter != "all" and task_filter.lower() != task_name:
            continue

        task_entry = dict(task)
        task_entry["source"] = "api"

        pipeline_key = task_pipelines.get(i)
        if not pipeline_key:
            tasks_with_logs.append(task_entry)
            continue

        pipeline_id, pipeline_run_id = pipeline_key
        cache_key = f"{pipeline_id}/{pipeline_run_id}"

        # 缓存 pipeline detail 请求
        if cache_key not in pipeline_details:
            try:
                pipeline_details[cache_key] = get_pipeline_run_detail(
                    token, domain_id, pipeline_id, pipeline_run_id
                )
            except CodeArtsAPIError as e:
                pipeline_details[cache_key] = {"_error": e.message}

        detail = pipeline_details[cache_key]

        if "_error" in detail:
            task_entry["api_error"] = detail["_error"]
            tasks_with_logs.append(task_entry)
            continue

        # 建立 job name → job id 映射
        jobs = detail.get("jobs", [])
        job_map = {}
        for job in jobs:
            jname = job.get("name", "")
            jid = job.get("id", "")
            jstatus = job.get("status", "")
            if jname and jid:
                job_map[jname.lower()] = {
                    "id": jid, "status": jstatus, "name": jname,
                }

        # 尝试精确匹配或模糊匹配
        matched_job = job_map.get(task_name)
        if not matched_job:
            for jname, jinfo in job_map.items():
                if task_name in jname or jname in task_name:
                    matched_job = jinfo
                    break

        if matched_job:
            # 只为失败的任务拉取完整日志
            if task["status"] == "fail":
                try:
                    log_text = fetch_full_job_log(
                        token, domain_id, pipeline_id,
                        pipeline_run_id, matched_job["id"],
                    )
                    task_entry["log"] = log_text
                except CodeArtsAPIError as e:
                    task_entry["api_error"] = e.message
            task_entry["job_id"] = matched_job["id"]
            task_entry["job_status"] = matched_job["status"]

        tasks_with_logs.append(task_entry)

    return {
        "source": "api",
        "repo": repo,
        "pr": pr,
        "tasks": tasks_with_logs,
    }


def fetch(repo: str, pr: int, source: str = "auto", task_filter: str = "all") -> dict:
    """Fetch CI results using specified source with fallback.

    source="auto": 尝试 API，失败则降级到评论解析。
    source="api":  只用 API（需要 CODEARTS_DOMAIN_ID）。
    source="comments": 只从评论解析。
    """
    if source == "api":
        return fetch_from_api(repo, pr, task_filter)
    elif source == "comments":
        return fetch_from_comments(repo, pr, task_filter)
    else:  # auto
        try:
            result = fetch_from_api(repo, pr, task_filter)
            # 如果 API 返回了带日志的结果，使用它
            if result.get("tasks") and not result.get("error"):
                return result
            # 否则降级到评论
            return fetch_from_comments(repo, pr, task_filter)
        except (NotImplementedError, CodeArtsAPIError):
            return fetch_from_comments(repo, pr, task_filter)


def main():
    parser = argparse.ArgumentParser(description="Fetch CI logs for CANN PRs")
    parser.add_argument("--repo", required=True, help="repo e.g. cann/hcomm")
    parser.add_argument("--pr", type=int, required=True, help="PR number")
    parser.add_argument("--source", choices=["auto", "api", "comments"],
                        default="auto", help="Log source (default: auto)")
    parser.add_argument("--task", default="all",
                        help="Filter by task name (default: all)")
    parser.add_argument("--domain-id", default="",
                        help="CodeArts domain_id (overrides CODEARTS_DOMAIN_ID env)")
    parser.add_argument("--log", action="store_true",
                        help="Print full log content for failed tasks")

    args = parser.parse_args()

    if args.domain_id:
        global CODEARTS_DOMAIN_ID
        CODEARTS_DOMAIN_ID = args.domain_id

    result = fetch(args.repo, args.pr, args.source, args.task)

    if args.log:
        # 单独打印失败任务的日志（日志太长不适合放在 JSON 里）
        for task in result.get("tasks", []):
            if task.get("log"):
                print(f"=== {task['name']} (status: {task['status']}) ===")
                print(task["log"])
                print()
        if not any(t.get("log") for t in result.get("tasks", [])):
            print("无可用日志。使用 --source api 且设置 CODEARTS_DOMAIN_ID 来获取。")
    else:
        # 默认输出 JSON（但截断超长日志摘要）
        output = dict(result)
        output["tasks"] = []
        for task in result.get("tasks", []):
            t = dict(task)
            if "log" in t and len(t["log"]) > 2000:
                t["log_preview"] = t["log"][:2000] + f"\n... ({len(t['log'])} chars total)"
                del t["log"]
            output["tasks"].append(t)
        print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
