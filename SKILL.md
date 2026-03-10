---
name: vibe-pr
description: "CANN 社区自主开发 bot。能接需求、自主开发、自我检视、管理 PR 全生命周期。当用户提到 'cann'、'hccl'、'hcomm'、'提交 PR'、'触发 CI'、'找 reviewer'、'PR 状态'、'CLA'、'issue'，或在 cann/* 仓库下操作时触发。覆盖：需求分析、代码开发、自检、fork+push、创建 PR、CLA 检查、触发 CI、CI 失败修复、review 响应、分析 reviewer 活跃度并推荐、跟踪合并状态。"
---

# CANN 社区自主开发 Bot

从需求到合并的完整闭环：

```
issue → 理解需求 → 定位代码 → 写代码 → 自检 → 提交 PR
  ↑                                                    ↓
  │                                              触发 CI
  │                                                    ↓
  │                                         CI 通过？──否──→ ci-analyzer agent ──┐
  │                                              ↓ 是                           │
  │                                         请求 review                    重新提交 ←┘
  │                                              ↓
  │                                    reviewer 有意见？──是──→ review-responder agent ──┐
  │                                              ↓ 否                                   │
  │                                         四标签就位                              重新提交 ←┘
  │                                              ↓
  └──────────────────────────────── squash merge ← /check-pr
```

自治边界——什么该自动，什么该问人：
- 全自动：读 issue、定位代码、按规范写代码、自检、创建 PR、触发 CI、CI 失败后修编译错误/静态检查问题、查 PR 状态
- 需要确认：需求理解是否正确、代码改动方案、响应 reviewer 的设计层面质疑、CI 失败原因不明时的修复策略
- 必须人来：选择找谁 review（人情世故）、在 PR 评论区 @ reviewer、处理与 reviewer 的分歧、CLA 签署、决定是否 force push

详细 workflow 参考见 [references/workflow.md](references/workflow.md)。

脚本目录：`~/.claude/skills/vibe-pr/scripts/`
API 封装：`gitcode_api.py`（认证、GET/POST、分页全在里面，直接 import 使用）

---

## 跨 session 上下文

每个活跃 PR 有一个 context 文件（`~/.claude/skills/vibe-pr/context/`）记录工作进度和决策。

session 开始时，先读取 context：
```bash
python3 ~/.claude/skills/vibe-pr/scripts/task_context.py --repo <repo> --pr <number>
```

没有 context 文件时，用 `--init` 从当前 PR 状态生成：
```bash
python3 ~/.claude/skills/vibe-pr/scripts/task_context.py --repo <repo> --pr <number> --init
```

不确定在处理哪个 PR 时，列出所有活跃 context：
```bash
python3 ~/.claude/skills/vibe-pr/scripts/task_context.py --list
```

里程碑更新：阶段推进、重要决策、遇到阻塞时，用 Edit 工具更新 context 文件。
只记录跨 session 会丢失的信息（用户确认的方案、reviewer 关系、失败原因），不记录可从 API 重新获取的信息。

---

## Phases

| Phase | Name | Trigger | Details |
|-------|------|---------|---------|
| 0 | 需求接入 | New issue or user request | @references/phase-0-requirements.md |
| 1 | Fork + Push | Code ready | @references/workflow.md (Fork + Push section) |
| 1.5 | 自检 | Before PR creation | @references/phase-1-selfcheck.md |
| 2 | 创建 PR | Code pushed | See below |
| 3 | CLA 检查 | PR created | See below |
| 4 | 触发 CI | CLA signed | @references/phase-4-ci.md |
| 5 | 找 Reviewer + Review 响应 | CI passed | @references/phase-5-reviewer.md |
| 6 | 跟踪状态 | Reviewer assigned | See below |

---

## Agent Dispatch

| Situation | Agent/Script | Input |
|-----------|-------------|-------|
| New review suggestions/questions | review-responder agent | comment_parser output (JSON) |
| CI failure | ci-analyzer agent | ci_log_fetcher output (JSON) |
| Multi-PR overview | pr_dashboard.py (script) | -- |
| Review status check | review_tracker.py (script) | -- |
| Monitor PR for new comments | pr_monitor.py --once (script) | -- |

When dispatching an agent, pass repo, pr, repo_root, and relevant data as JSON prompt.

---

## Phase 1: Fork + Push

1. Confirm target repo (e.g. `cann/hcomm`) and branch name
2. Check fork remote: `git remote -v`
3. No fork? `gitcode_api.create_fork(repo, token)`
4. Add remote and push:
   ```bash
   git remote add fork https://gitcode.com/<username>/hcomm.git
   git push -u fork <branch-name>
   ```

Token from `~/.git-credentials` (format `https://username:token@gitcode.com`), use `gitcode_api.get_token()`.

---

## Phase 2: Create PR

先检查 push 时是否已自动创建 MR：
```python
pulls = list_pulls('<repo>', get_token(), state='open', head='<username>:<branch>')
```

已有 PR → `update_pull(repo, token, number, title='...', body='...')`

没有 → `create_pull(repo, token, title, head, base, body)`
- head 格式：`"用户名:分支名"`（如 `fan33:fix/xxx`）
- base 通常是 `master`
- 返回 409 时从错误信息提取 MR 编号 `!NNN`，改用 `update_pull`

---

## Phase 3: CLA Check

PR 创建后 cann-robot 秒级自动检查。如果失败：
1. 检查 commit 的 committer email 是否与 CLA 签署邮箱一致
2. 修正并 force push：
   ```bash
   git -c user.email="cla-email@example.com" commit --amend --reset-author --no-edit
   git push fork <branch> --force
   ```
3. 评论 `/check-cla` 重新检查

CLA 签署页面：https://clasign.osinfra.cn/sign/68cbd4a3dbabc050b436cdd4
CLA 只能在浏览器签署，无法 API 代签。

---

## Phase 6: Track Status

```bash
python3 ~/.claude/skills/vibe-pr/scripts/pr_status.py --repo cann/hcomm --pr <number>
```

四个标签全部就位时 cann-robot 自动 squash merge：
- `cann-cla/yes` — CLA
- `ci-pipeline-passed` — CI
- `lgtm` — 代码审查
- `approved` — 合并授权

长时间无人 review：先检查 PR 大小和描述质量，考虑 SIG 例会（双周五 14:00）提一嘴，或邮件列表 hccl@cann.osinfra.cn 发简短说明。

---

## Script Reference

| Script | Purpose | Example |
|--------|---------|---------|
| gitcode_api.py | API layer | (imported by other scripts) |
| issue_parser.py | Parse issue | `python3 scripts/issue_parser.py --repo X --issue N` |
| pr_status.py | PR status | `python3 scripts/pr_status.py --repo X --pr N` |
| comment_parser.py | Classify comments | `python3 scripts/comment_parser.py --repo X --pr N --since-commit` |
| reviewer_activity.py | Reviewer analysis | `python3 scripts/reviewer_activity.py --repo X --pr N --recent 30` |
| task_context.py | Cross-session context | `python3 scripts/task_context.py --repo X --pr N --init` |
| review_tracker.py | Review status board | `python3 scripts/review_tracker.py --repo X --pr N --summary` |
| ci_log_fetcher.py | CI log extraction | `python3 scripts/ci_log_fetcher.py --repo X --pr N --source auto` |
| pr_dashboard.py | Multi-PR dashboard | `python3 scripts/pr_dashboard.py --active` |
| pr_monitor.py | Monitor + dispatch | `python3 scripts/pr_monitor.py --repo X --pr N --once` |

---

## Merge Troubleshooting

| Symptom | Fix |
|---------|-----|
| squash conflict | rebase target branch, re-push |
| concurrent merge conflict | wait 1 min, comment `/check-pr` |
| title has `[WIP]` | remove `[WIP]` prefix |
| unresolved review threads | resolve all CodeReview discussions |
| CI retry button fails | re-comment `/compile` for full re-run |

Details in [references/workflow.md](references/workflow.md).

---

## Bot Commands

| Command | Function |
|---------|----------|
| `/compile` | Trigger CI |
| `/lgtm` / `/lgtm cancel` | Approve review / revoke |
| `/approve` / `/approve cancel` | Approve merge / revoke |
| `/check-cla` | Re-check CLA |
| `/check-pr` | Check labels and trigger merge |
| `/assign @user` | Assign issue |
| `/close` | Close issue |
