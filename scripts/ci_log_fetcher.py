#!/usr/bin/env python3
"""CI log fetcher for CANN PRs on openLiBing platform.

三层获取策略：
1. comments — 从 PR 评论中解析 cann-robot 的 HTML 表格（无需认证）
2. api — 调 openLiBing CICD API 获取 pipeline 结构和调度层日志（无需认证）
     - pipeline-run/detail: job 级别状态和错误消息
     - pipeline/logs: 调度层日志（非构建编译输出）
     - 构建层日志（编译器stderr）在 Build 子服务中，需要浏览器认证，当前不可用
3. diff — 分析 PR diff 推断编译错误原因（当 API 无法提供编译输出时的 fallback）

projectId 映射（通过探测确认）：CANN → 300033
logs API 参数：jobRunId=job.id, stepRunId=step.id（不是 dispatch_id）
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

# Bot accounts that post CI results
BOT_AUTHORS = {"cann-robot", "ascend-robot"}

# Emoji → status mapping (covers both unicode chars and HTML entities)
EMOJI_STATUS = {
    "✅": "pass",  "\u2705": "pass",  "&#9989;": "pass",
    "❌": "fail",  "\u274c": "fail",  "&#10060;": "fail",
    "🛑": "blocked", "\U0001f6d1": "blocked", "&#128721;": "blocked",
    "🕖": "pending", "\U0001f556": "pending", "&#128346;": "pending",
    "SUCCESS": "pass", "FAILED": "fail", "RUNNING": "running",
    "ERROR": "fail", "TIMEOUT": "fail", "ABORTED": "fail",
}

# Pattern to parse HTML table rows from CI result comments.
# cann-robot format: <td><strong>name</strong></td><td>✅ SUCCESS</td><td><a href=URL>...</a></td>
# URL may or may not be quoted (href=URL or href="URL")
CI_TABLE_ROW = re.compile(
    r"<td>(?:<strong>)?([^<]+?)(?:</strong>)?</td>\s*"  # task name
    r"<td>\s*([^<]+?)\s*</td>\s*"                       # status (emoji + keyword)
    r'<td>\s*<a\s+href=["\']?([^"\'>]*)["\']?[>\s]',    # detail URL (quoted or unquoted)
    re.IGNORECASE | re.DOTALL,
)

# Fallback: detect CI trigger comment (no results table yet)
CI_TRIGGER_PATTERN = re.compile(r"流水线任务触发成功")


def _resolve_status(raw: str) -> str:
    """Map raw status text (emoji, HTML entity, or keyword) to canonical status."""
    raw = raw.strip()
    # Direct lookup
    if raw in EMOJI_STATUS:
        return EMOJI_STATUS[raw]
    # Try uppercase keyword
    upper = raw.upper()
    if upper in EMOJI_STATUS:
        return EMOJI_STATUS[upper]
    # Strip non-alnum and retry (e.g. "✅ SUCCESS" → check both parts)
    for part in raw.split():
        part = part.strip()
        if part in EMOJI_STATUS:
            return EMOJI_STATUS[part]
        if part.upper() in EMOJI_STATUS:
            return EMOJI_STATUS[part.upper()]
    return raw.lower()


def fetch_from_comments(repo: str, pr: int, task_filter: str = "all") -> dict:
    """Extract CI results from the latest bot HTML table comment."""
    token = gitcode_api.get_token()
    comments = gitcode_api.get_pull_comments(repo, token, pr)

    # Walk newest-first to find the latest CI result table
    for comment in reversed(comments):
        body = comment.get("body", "")
        # GitCode API: author 字段为空 dict，实际数据在 user 字段
        user = comment.get("user") or comment.get("author") or {}
        username = user.get("login", "") if isinstance(user, dict) else ""

        if username not in BOT_AUTHORS:
            continue

        if "<table" not in body:
            continue

        tasks = []
        for match in CI_TABLE_ROW.finditer(body):
            task_name = match.group(1).strip()
            raw_status = match.group(2).strip()
            log_url = match.group(3).strip() if match.group(3) else ""

            # Skip header rows and stage-only rows
            if task_name.lower() in ("任务名", "阶段", "状态", "详情"):
                continue

            status = _resolve_status(raw_status)
            task_key = task_name.lower()

            if task_filter != "all" and task_filter.lower() != task_key:
                continue

            task_entry = {
                "name": task_name,
                "status": status,
                "log_url": log_url,
                "comment_id": comment.get("id"),
            }
            # Extract jobRunId/stepRunId from URL for direct API access
            if log_url:
                url_params = parse_pipeline_url(log_url)
                if url_params.get("job_run_id"):
                    task_entry["job_run_id"] = url_params["job_run_id"]
                if url_params.get("step_run_id"):
                    task_entry["step_run_id"] = url_params["step_run_id"]

            tasks.append(task_entry)

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


# ── Cookie 提取 ─────────────────────────────────────────

def extract_chrome_cookie(domain: str = "openlibing.com") -> str:
    """从 Chrome cookie 数据库提取指定域名的 cookie 字符串。

    macOS only。需要 cryptography 库。Chrome 需未运行或已关闭 cookie DB 锁。
    返回 "name=value; name2=value2" 格式的 cookie 字符串。
    """
    try:
        import hashlib, shutil, sqlite3, subprocess, tempfile
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.backends import default_backend
    except ImportError:
        return ""

    db_path = os.path.expanduser(
        "~/Library/Application Support/Google/Chrome/Default/Cookies"
    )
    if not os.path.exists(db_path):
        return ""

    password = subprocess.check_output(
        ["security", "find-generic-password", "-w", "-s", "Chrome Safe Storage"],
        text=True,
    ).strip()
    key = hashlib.pbkdf2_hmac("sha1", password.encode(), b"saltysalt", 1003, dklen=16)

    tmp = tempfile.mktemp(suffix=".db")
    shutil.copy2(db_path, tmp)
    conn = sqlite3.connect(tmp)
    rows = conn.execute(
        "SELECT name, encrypted_value FROM cookies WHERE host_key LIKE ?",
        (f"%{domain}%",),
    ).fetchall()
    conn.close()
    os.unlink(tmp)

    parts = []
    for name, ev in rows:
        if not ev or len(ev) < 4 or ev[:3] != b"v10":
            continue
        iv = b" " * 16
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        plain = cipher.decryptor().update(ev[3:]) + cipher.decryptor().finalize()
        # 重新解密（decryptor 是一次性的）
        cipher2 = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        d = cipher2.decryptor()
        plain = d.update(ev[3:]) + d.finalize()
        pad_len = plain[-1]
        if 0 < pad_len <= 16:
            plain = plain[:-pad_len]
        # Chrome v10: 解密后值中可能有签名前缀，用 '.' 分隔取最后部分
        text = plain.decode("utf-8", errors="replace")
        if "." in text and text.index(".") < 40:
            text = text[text.index(".") + 1:]
        parts.append(f"{name}={text}")

    return "; ".join(parts)


# ── openLiBing API ──────────────────────────────────────

# openLiBing CICD API（公开，无需认证）
OPENLIBING_API = "https://www.openlibing.com/gateway/openlibing-cicd"
OPENLIBING_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
# 保留向后兼容
CODEARTS_BASE = os.environ.get("CODEARTS_BASE", "https://www.openlibing.com")
CODEARTS_DOMAIN_ID = os.environ.get("CODEARTS_DOMAIN_ID", "")
OPENLIBING_COOKIE = os.environ.get("OPENLIBING_COOKIE", "")

# projectName → projectId 映射（通过API探测确认）
PROJECT_ID_MAP = {"CANN": "300033"}


def parse_pipeline_url(url: str) -> dict:
    """从 openLiBing 流水线 URL 提取所有参数。

    URL 格式：
        https://www.openlibing.com/apps/pipelineDetail?
            projectId=4&pipelineId=XXX&pipelineRunId=YYY
            &stepId=ZZZ&jobRunId=AAA&stepRunId=BBB
            &codeHostingPlatformFlag=gitcode
    """
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    # Map URL param names → snake_case keys
    key_map = {
        "projectId":    "project_id",
        "pipelineId":   "pipeline_id",
        "pipelineRunId": "pipeline_run_id",
        "stepId":       "step_id",
        "jobRunId":     "job_run_id",
        "stepRunId":    "step_run_id",
        "projectName":  "project_name",
    }
    result = {}
    for url_key, dict_key in key_map.items():
        if url_key in params:
            result[dict_key] = params[url_key][0]
    return result


def _codearts_request(method, url, token, data=None, cookie=None):
    """发送 CodeArts/openLiBing API 请求。

    认证优先级：
    1. Cookie（浏览器 session，用于 /gateway API）
    2. PRIVATE-TOKEN + X-Auth-Token（gitcode/华为云 token）
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    cookie = cookie or OPENLIBING_COOKIE
    if cookie:
        headers["Cookie"] = cookie
    if token:
        headers["PRIVATE-TOKEN"] = token
        headers["X-Auth-Token"] = token
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            if not raw.strip():
                return {}
            # SPA fallback 检测：如果返回 HTML 说明 API 路径不对
            if raw.strip().startswith("<!doctype") or raw.strip().startswith("<html"):
                raise CodeArtsAPIError(
                    "API 返回 HTML 而非 JSON，可能路径错误或需要认证",
                    status_code=200,
                )
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


def _openlibing_get(path: str, params: dict) -> dict:
    """GET openLiBing CICD API (public, no auth required)."""
    qs = urllib.parse.urlencode(params)
    url = f"{OPENLIBING_API}{path}?{qs}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": OPENLIBING_UA,
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("code") != 200:
                raise CodeArtsAPIError(
                    f"{data.get('msg', 'unknown error')} (code={data.get('code')})")
            return data.get("data", {})
    except urllib.error.HTTPError as e:
        raise CodeArtsAPIError(f"HTTP {e.code}", status_code=e.code)
    except (urllib.error.URLError, OSError) as e:
        raise CodeArtsAPIError(f"网络错误: {e}")


def _openlibing_post(path: str, body: dict) -> dict:
    """POST openLiBing CICD API (public, no auth required)."""
    url = f"{OPENLIBING_API}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": OPENLIBING_UA,
    }, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("code") != 200:
                raise CodeArtsAPIError(
                    f"{result.get('msg', 'unknown error')} (code={result.get('code')})")
            return result.get("data", {})
    except urllib.error.HTTPError as e:
        raise CodeArtsAPIError(f"HTTP {e.code}", status_code=e.code)
    except (urllib.error.URLError, OSError) as e:
        raise CodeArtsAPIError(f"网络错误: {e}")


def get_pipeline_run_detail(project_id, pipeline_id, pipeline_run_id):
    """获取流水线运行详情（含 stages/jobs/steps 结构和错误消息）。"""
    return _openlibing_get("/project/pipeline/pipeline-run/detail", {
        "projectId": project_id,
        "pipelineId": pipeline_id,
        "pipelineRunId": pipeline_run_id,
    })


def get_pipeline_logs(project_id, pipeline_id, pipeline_run_id,
                      job_run_id, step_run_id, start_offset="0"):
    """获取流水线调度层日志（POST /project/pipeline/logs）。"""
    return _openlibing_post("/project/pipeline/logs", {
        "projectId": project_id,
        "pipelineId": pipeline_id,
        "pipelineRunId": pipeline_run_id,
        "jobRunId": job_run_id,
        "stepRunId": step_run_id,
        "startOffset": start_offset,
    })


def fetch_from_api(repo: str, pr: int, task_filter: str = "all") -> dict:
    """从 openLiBing CICD API 获取 CI 详情、错误消息和调度层日志。

    流程：
    1. 从 PR 评论解析任务列表和 pipeline URL 参数
    2. 用 PROJECT_ID_MAP 补全 projectId（评论 URL 只有 projectName）
    3. 调 pipeline-run/detail 获取 job/step 级别的错误消息
    4. 对失败 job 用 job.id + step.id 拉调度层日志

    注意：调度层日志只含 "start task / poll status / task failed" 信息，
    不含实际编译器输出（编译输出在 Build 子服务中，需要浏览器认证）。
    """
    comment_result = fetch_from_comments(repo, pr, task_filter="all")
    if not comment_result["tasks"]:
        return {"source": "api", "repo": repo, "pr": pr, "tasks": [],
                "error": "PR 评论中未找到 CI 结果"}

    # 从评论 URL 中提取 pipeline 参数
    first_url = ""
    for t in comment_result["tasks"]:
        if t.get("log_url") and "pipelineRunId" in t.get("log_url", ""):
            first_url = t["log_url"]
            break
    if not first_url:
        return {"source": "api", "repo": repo, "pr": pr,
                "tasks": comment_result["tasks"],
                "error": "评论中未找到有效的流水线 URL"}

    url_params = parse_pipeline_url(first_url)
    pipeline_id = url_params.get("pipeline_id", "")
    pipeline_run_id = url_params.get("pipeline_run_id", "")

    # projectId: 评论 URL 只有 projectName，需要映射到数字 ID
    project_id = url_params.get("project_id", "")
    if not project_id:
        project_name = url_params.get("project_name", "")
        project_id = PROJECT_ID_MAP.get(project_name, "")

    if not all([project_id, pipeline_id, pipeline_run_id]):
        return {"source": "api", "repo": repo, "pr": pr,
                "tasks": comment_result["tasks"],
                "error": f"缺少必要参数 (projectId={project_id})"}

    # 获取 pipeline-run/detail（含 job/step 结构和错误消息）
    try:
        detail = get_pipeline_run_detail(project_id, pipeline_id, pipeline_run_id)
    except CodeArtsAPIError as e:
        return {"source": "api", "repo": repo, "pr": pr,
                "tasks": comment_result["tasks"],
                "error": f"API 调用失败: {e.message}"}

    # 建立 job name → {message, build_job_id, job_id, step_id} 映射
    job_info = {}
    for stage in detail.get("stages", []):
        for job in stage.get("jobs", []):
            name = job.get("name", "")
            info = {
                "message": job.get("message", ""),
                "status": job.get("status", ""),
                "job_id": job.get("id", ""),  # 用于 logs API
            }
            for step in job.get("steps", []):
                info["step_id"] = step.get("id", "")  # 用于 logs API
                if step.get("message"):
                    info["step_message"] = step["message"]
                for inp in step.get("inputs", []):
                    if inp["key"] == "jobId":
                        info["build_job_id"] = inp["value"]
            job_info[name] = info

    # 合并评论解析结果和 API 详情
    tasks = []
    for task in comment_result["tasks"]:
        if task_filter != "all" and task_filter.lower() != task["name"].lower():
            continue
        entry = dict(task)
        entry["source"] = "api"

        api_info = job_info.get(task["name"], {})
        if api_info.get("message"):
            entry["error_message"] = api_info["message"]
        if api_info.get("build_job_id"):
            entry["build_job_id"] = api_info["build_job_id"]

        # 对失败任务用 job.id + step.id 获取调度层日志
        job_id = api_info.get("job_id", "")
        step_id = api_info.get("step_id", "")
        if task["status"] == "fail" and job_id and step_id:
            try:
                log_data = get_pipeline_logs(
                    project_id, pipeline_id, pipeline_run_id,
                    job_id, step_id)
                log_text = log_data.get("log", "")
                if log_text:
                    entry["log"] = log_text
            except CodeArtsAPIError:
                pass

        tasks.append(entry)

    return {"source": "api", "repo": repo, "pr": pr,
            "pipeline_id": pipeline_id, "pipeline_run_id": pipeline_run_id,
            "project_id": project_id, "tasks": tasks}


def fetch(repo: str, pr: int, source: str = "auto", task_filter: str = "all") -> dict:
    """Fetch CI results using specified source with fallback.

    source="auto": 尝试 API，失败则降级到评论解析。
    source="api":  只用 openLiBing API。
    source="comments": 只从评论解析。
    """
    if source == "api":
        return fetch_from_api(repo, pr, task_filter)
    elif source == "comments":
        return fetch_from_comments(repo, pr, task_filter)
    else:  # auto
        try:
            result = fetch_from_api(repo, pr, task_filter)
            if result.get("tasks") and not result.get("error"):
                return result
            return fetch_from_comments(repo, pr, task_filter)
        except (CodeArtsAPIError, Exception):
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
    parser.add_argument("--cookie", default="",
                        help="Browser cookie for openLiBing API")
    parser.add_argument("--auto-cookie", action="store_true",
                        help="Auto-extract cookie from Chrome (macOS)")
    parser.add_argument("--log", action="store_true",
                        help="Print full log content for failed tasks")
    parser.add_argument("--failed-only", action="store_true",
                        help="Only show failed tasks")

    args = parser.parse_args()

    if args.domain_id:
        global CODEARTS_DOMAIN_ID
        CODEARTS_DOMAIN_ID = args.domain_id
    if args.cookie:
        global OPENLIBING_COOKIE
        OPENLIBING_COOKIE = args.cookie
    elif args.auto_cookie:
        OPENLIBING_COOKIE = extract_chrome_cookie("openlibing.com")
        if OPENLIBING_COOKIE:
            print(f"[cookie] 从 Chrome 提取到 openlibing.com cookie", file=sys.stderr)

    result = fetch(args.repo, args.pr, args.source, args.task)

    if args.failed_only:
        result["tasks"] = [t for t in result.get("tasks", [])
                           if t.get("status") == "fail"]

    if args.log:
        for task in result.get("tasks", []):
            if task.get("log"):
                print(f"=== {task['name']} (status: {task['status']}) ===")
                print(task["log"])
                print()
        if not any(t.get("log") for t in result.get("tasks", [])):
            print("无可用日志。API 端点待确认，当前仅支持评论解析模式。")
    else:
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
