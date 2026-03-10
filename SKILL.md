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
  │                                         CI 通过？──否──→ 分析失败 → 修代码 ──┐
  │                                              ↓ 是                           │
  │                                         请求 review                    重新提交 ←┘
  │                                              ↓
  │                                    reviewer 有意见？──是──→ 理解意见 → 改代码 ──┐
  │                                              ↓ 否                             │
  │                                         四标签就位                        重新提交 ←┘
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

## 阶段 0：需求接入

从 issue 或用户口述的需求开始，分析需求，生成改动计划，向用户确认后再动手。

### 0.1 获取需求

如果需求来自 issue：
```bash
python3 ~/.claude/skills/vibe-pr/scripts/issue_parser.py --repo cann/hcomm --issue 123
# 或直接传 URL
python3 ~/.claude/skills/vibe-pr/scripts/issue_parser.py --url https://gitcode.com/cann/hcomm/issues/123
```

输出 JSON 包含：标题、描述、标签、指派人、评论。

如果需求来自用户口述：直接从对话中提取关键信息。

### 0.2 分析需求

从需求中提取：
- 问题现象（什么坏了 / 缺什么功能）
- 复现条件（如果是 bug）
- 期望行为（改完应该怎样）

### 0.3 定位代码

克隆/更新目标仓库（如果本地没有），定位相关代码：
- 根据错误信息、函数名、模块名搜索
- 读代码理解现有逻辑
- 识别需要修改的文件

### 0.4 生成改动计划

向用户展示改动计划，包括：
- 要改的文件列表
- 每个文件的改动意图
- 风险点（可能影响其他模块的地方）

等用户确认后再进入开发阶段。简单 bug（一行修复、变量名错误、明显的复制粘贴错误）可以直接出 PR，事后通知。

---

## 阶段 1：准备（Fork + Push）

当用户要向 CANN 仓库提交代码时：

1. 确认目标仓库（如 `cann/hcomm`）和分支名
2. 检查是否已有 fork remote：`git remote -v`
3. 如果没有 fork，用 `gitcode_api.create_fork(repo, token)` 创建
4. 添加 fork remote 并 push：
   ```bash
   git remote add fork https://gitcode.com/<username>/hcomm.git
   git push -u fork <branch-name>
   ```

关键：token 从 `~/.git-credentials` 读取（格式 `https://username:token@gitcode.com`），用 `gitcode_api.get_token()` 获取。

完成后更新 context：标记阶段 1 完成，记录 branch 名。

---

## 阶段 1.5：自检

写完代码、push 之前，对自己的改动做一轮检视。发现问题自动修复，修不了的报告给用户。

### 1.5.1 调用 vibe-review skill

对本地 diff 调用 vibe-review skill（`/vibe-review`），让它按 CANN C++ 编码规范审查自己的代码。vibe-review skill 已有完整能力：
- 1124 行编码规范（命名、格式、安全、HCCL 项目规则）
- 12 个 HCCL 高频缺陷模式
- 5 步审查方法论（理解上下文 → 工具验证 → 分层检查 → 置信度标注 → 报告）

具体操作：
1. 用 `git diff` 生成改动的 diff
2. 调用 vibe-review skill 对 diff 做检视
3. 对发现的问题逐条修复
4. 重新 diff 确认修复完成

### 1.5.2 vibe-review skill 不覆盖的检查

额外检查这两项（vibe-review skill 面向 review 场景，不检查这些）：

版权头检查：所有新建的 .h/.cpp/.cc/.cxx 文件必须有 CANN Open Software License Agreement Version 2.0 版权声明头（模板见 workflow.md）。

Commit message 格式检查：必须符合 `<type>(<scope>): <subject>` 格式，type 取值：feat / fix / docs / style / refactor / perf / test / chore。

### 1.5.3 自检通过标准

- vibe-review skill 报告的"严重"问题全部修复
- "一般"问题全部修复（自己的代码没理由留一般问题）
- "建议"问题视情况修复
- 版权头和 commit message 格式正确
- 通过后再 push 和创建 PR

---

## 阶段 2：创建或更新 PR

### 2.1 先检查是否已有 PR

GitCode 在 push 时可能自动为分支创建 MR（push 输出中会显示 MR 链接）。在调用 `create_pull` 之前，先检查该分支是否已有 open PR：

```bash
python3 -c "
from gitcode_api import get_token, list_pulls
pulls = list_pulls('<repo>', get_token(), state='open', head='<username>:<branch>')
for p in pulls:
    print(f'!{p[\"number\"]}: {p[\"title\"]}')
"
```

如果已有 PR，用 `update_pull` 更新标题和描述即可：
```bash
python3 -c "
from gitcode_api import get_token, update_pull
update_pull('<repo>', get_token(), <number>, title='...', body='...')
"
```

### 2.2 创建新 PR

如果没有已有 PR，用 `create_pull` 创建：

```python
gitcode_api.create_pull(repo, token, title, head, base, body)
```

关键参数：
- 认证用 `PRIVATE-TOKEN` header（gitcode_api 已封装）
- head 格式必须是 `"用户名:分支名"`（如 `fan33:fix/xxx`）
- base 通常是 `master`

如果 `create_pull` 返回 409（"Another open merge request already exists"），从错误信息中提取 MR 编号（格式 `!NNN`），改用 `update_pull` 更新。

### 2.3 规范

Commit message 格式：`<type>(<scope>): <subject>`，type 取值：feat / fix / docs / style / refactor / perf / test / chore。

新建源码文件需添加 CANN Open Software License Agreement Version 2.0 版权声明头（模板见 workflow.md）。

完成后更新 context：标记阶段 2 完成，记录 PR 编号。

---

## 阶段 3：CLA 检查

PR 创建后 cann-robot 秒级自动检查 CLA。如果失败：

1. 检查 commit 的 committer email 是否与 CLA 签署邮箱一致
2. 如果不一致，修正并 force push：
   ```bash
   git -c user.email="cla-email@example.com" commit --amend --reset-author --no-edit
   git push fork <branch> --force
   ```
3. 在 PR 评论区输入 `/check-cla` 触发重新检查

CLA 签署页面：https://clasign.osinfra.cn/sign/68cbd4a3dbabc050b436cdd4
查询 API：`GET https://clasign.osinfra.cn/api/v1/individual-signing/68cbd4a3dbabc050b436cdd4?email=<email>`

注意：CLA 签署只能在浏览器完成，无法通过 API 代签。如果用户未签署，提示签署页面 URL。

---

## 阶段 4：触发 CI

CI 不会自动触发。在 PR 评论区发送 `compile`：

```bash
python3 -c "
from gitcode_api import get_token, post_comment
post_comment('<repo>', get_token(), <pr_number>, 'compile')
"
```

CI 运行约 20-30 分钟。通过后自动打标签 `ci-pipeline-passed`。

用 pr_status.py 检查 CI 状态：
```bash
python3 ~/.claude/skills/vibe-pr/scripts/pr_status.py --repo cann/hcomm --pr <number>
```

注意：每次新 push 会移除 `ci-pipeline-passed`，需重新触发 CI。

### CI 失败处理

CI 失败后不要直接找用户——先尝试自主分析和修复。

#### 4.1 获取失败信息

CI 结果有两个来源：
1. cann-robot 在 PR 评论区留下的 CI 结果摘要（评论中包含任务状态表格）
2. PR 标签变化：`ci-pipeline-failed` 表示失败

用 comment_parser.py 提取 CI 结果评论：
```bash
python3 ~/.claude/skills/vibe-pr/scripts/comment_parser.py --repo cann/hcomm --pr <number>
```
在输出中找 `"type": "ci_result"` 的评论，从中提取失败任务和错误信息。

CodeArts 平台的 CI 日志目前没有公开 API。如果评论区的信息不足以定位问题，让用户从 CodeArts 页面贴日志片段。

#### 4.2 分类修复

按失败类型采取不同策略：

编译错误（Compile_Ascend_X86 / Compile_Ascend_ARM 失败）：
- 从错误信息中提取文件名、行号、错误描述
- 定位代码，修复编译问题
- 常见原因：缺少 include、类型不匹配、未定义符号

静态检查（codecheck 失败）：
- 对照 CANN C++ 编码规范逐条修正
- 多数是命名、格式、安全函数替换等机械性问题
- 可以重新跑一遍自检（阶段 1.5）来覆盖

测试失败（UT/ST/Smoke 失败）：
- 区分是自己引入的回归还是已有的 flaky test
- 如果是自己的改动导致的：读测试代码理解期望行为，修改自己的代码
- 如果疑似已有问题：查该测试在 master 上是否也失败

PR 内容检查（Check_Pr 失败）：
- 通常是 commit message 格式、版权头、文件命名等问题
- 回到阶段 1.5 的检查项修正

#### 4.3 修复后重新提交

1. 修复代码
2. 重新自检（阶段 1.5）
3. push 更新
4. 重新触发 CI（评论 `compile`）
5. 等待 CI 结果

连续失败 2 次仍无法解决：升级给用户，附上失败日志和分析。不要无限循环。

修复后更新 context：记录 CI 失败原因和修复方式。

---

## 阶段 5：找 Reviewer

Review 不是打打杀杀，review 是人情世故。别把 reviewer 当成可以按排名挑选的资源——愿不愿意帮你看代码，取决于关系，不取决于排名。

### 5.1 先了解局面

```bash
python3 ~/.claude/skills/vibe-pr/scripts/pr_status.py --repo cann/hcomm --pr <number>
```

从输出中获取：候选人列表（`modules[].lgtm.candidates` / `approve.candidates`）、当前审批进度、最新 commit 时间。

```bash
python3 ~/.claude/skills/vibe-pr/scripts/reviewer_activity.py --repo cann/hcomm --pr <number> --recent 30
```

活跃度数据是背景信息，不是购物清单。解读时注意：
- review 数多 = 这个人已经很忙了，不等于随叫随到
- review 数少 ≠ 不愿意帮忙，可能只是没人找过
- 响应快说明对方重视社区，不是说对方闲

### 5.2 先想关系，再想人选

向用户了解（如果用户没主动说，主动问）：
- 你之前帮谁 review 过代码？（互惠是最强的请求理由）
- 你在 SIG 会议或邮件列表里跟谁打过交道？
- 有没有之前帮过你 review 的人？（维护关系比开拓新关系容易）

如果用户是新贡献者、还没有社区关系：建议先去 review 别人的 PR（哪怕只是阅读和提问），在 SIG 会议上露个面，再来请求 review。这不是浪费时间，这是投资。

### 5.3 推荐策略

结合关系和数据给出建议：

- lgtm 需要 2 人，推荐 3 人留冗余；approve 需要 1 人，推荐 2 人
- 优先推荐用户有互动基础的候选人
- 其次看谁对这个代码模块有 context（从最近 review 的 PR 类型判断）
- 最后才看活跃度排名

### 5.4 怎么请求 review

告知用户这些原则：
- 不要群发 @。一次 @ 两三个人是正常的，一次 @ 五个人是骚扰
- 说清楚改了什么、为什么改。reviewer 的时间比你的等待时间更宝贵
- PR 尽量小。500 行的 PR 没人想看，50 行的 PR 随手就批
- 考虑时机：hccl SIG 双周五 14:00 例会，会前/会后找人效果好；周末和节假日别催

如果长时间没人响应，先问自己三个问题：
1. PR 是不是太大了？能不能拆？
2. 描述够不够清楚？reviewer 能不能 30 秒内理解这个 PR 在做什么？
3. 自己最近有没有帮别人 review？

### 5.5 审批规则

- 每个模块需要至少 2 个 `/lgtm` + 1 个 `/approve`
- `/approve` 隐含 `/lgtm`
- 审批时间必须晚于最新 commit 时间（新 push 使旧审批失效）
- pr_status.py 输出的 `latest_commit_at` 可用于判断审批是否仍有效

完成后更新 context：记录推荐的 reviewer 和选择理由。

---

## 阶段 5.5：Review 响应

当 reviewer 在 PR 上留下评论后，分析评论类型并采取对应行动。

### 5.5.1 获取 review 意见

```bash
# 获取所有 review 评论
python3 ~/.claude/skills/vibe-pr/scripts/comment_parser.py --repo cann/hcomm --pr <number>

# 只看最新 push 之后的评论（过滤掉已处理的旧评论）
python3 ~/.claude/skills/vibe-pr/scripts/comment_parser.py --repo cann/hcomm --pr <number> --since-commit
```

comment_parser.py 会自动分类评论：
- `review_suggestion`：reviewer 的代码修改建议（包含代码块或修改指令）
- `review_question`：reviewer 的提问或设计质疑（包含问号或质疑性语句）
- `bot_auto` / `bot_command` / `ci_result` / `author_reply`：非 review 评论，忽略

### 5.5.2 处理策略

代码层面的建议（`review_suggestion`）——自主处理：
1. 理解 reviewer 要求改什么
2. 实施修改
3. 重新自检（阶段 1.5）
4. push 更新
5. 重新触发 CI
6. 在评论区说明改了什么（由用户发，bot 准备内容）

设计层面的质疑（`review_question`）——升级给用户：
1. 总结 reviewer 的问题
2. 分析 reviewer 的关切点
3. 提供可能的回应方向
4. 由用户决定如何回应（这涉及技术判断和社区关系，不能自作主张）

区分标准：如果 reviewer 说"改成 X"，这是建议，可以直接改。如果 reviewer 问"为什么不用 X"，这是质疑，需要用户决定。

### 5.5.3 注意事项

- 修改代码后新 push 会使之前的 lgtm/approve 失效，需要重新请求 review
- 不要代替用户在评论区回复（社交行为必须是用户本人）
- 准备好回复内容给用户，让用户复制粘贴或修改后发

---

## 阶段 6：跟踪状态

定期用 pr_status.py 检查合并进度：

```bash
python3 ~/.claude/skills/vibe-pr/scripts/pr_status.py --repo cann/hcomm --pr <number>
```

向用户报告四个标签的状态：
- `cann-cla/yes` — CLA
- `ci-pipeline-passed` — CI
- `lgtm` — 代码审查
- `approved` — 合并授权

当 `merge_ready: true` 时，cann-robot 会自动执行 squash merge。

如果长时间无人 review：
- 先检查 PR 本身：是否太大、描述是否清晰、标题是否准确
- 考虑在 SIG 例会（双周五 14:00）上提一嘴，或在邮件列表 hccl@cann.osinfra.cn 发一封简短说明
- 礼貌地单独 @ 一两个人跟进，不要群发催促
- 如果自己最近没帮别人 review 过，先去做一两个，再回来等

---

## 阶段 7：自动监控

用 pr_monitor.py 持续监控 PR 评论，自动修复 review 建议。把阶段 5.5（Review 响应）从手动变成自动轮询。

### 7.1 启动监控

```bash
# 默认：处理所有 review 评论，每 5 分钟轮询
python3 ~/.claude/skills/vibe-pr/scripts/pr_monitor.py \
  --repo cann/hcomm --pr 584

# 只处理人类 reviewer 评论
python3 ~/.claude/skills/vibe-pr/scripts/pr_monitor.py \
  --repo cann/hcomm --pr 584 --human-only

# 单次执行（调试用）
python3 ~/.claude/skills/vibe-pr/scripts/pr_monitor.py \
  --repo cann/hcomm --pr 584 --once
```

注意：需要在仓库目录下运行，或通过 `--work-dir` 指定仓库路径。

### 7.2 工作流程

1. 调 comment_parser.py --since-commit 拉取最新 push 后的评论
2. `review_suggestion` → 构造 prompt → `claude -p` 修复 → commit → push → 触发 CI
3. `review_question` → 打印通知，不自动修改（需要人决策）
4. 用 state 文件去重，避免重复处理同一条评论

### 7.3 安全机制

- 单 PR 最多 5 轮自动修复（`--max-rounds` 可调）
- 启动时检查工作目录无未提交改动
- Ctrl+C 干净退出，state 自动保存
- state 文件持久化在 `~/.claude/skills/vibe-pr/state/`

### 7.4 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| --repo | 必填 | 仓库名 |
| --pr | 必填 | PR 编号 |
| --interval | 300 | 轮询间隔（秒） |
| --max-rounds | 5 | 最大修复轮次 |
| --human-only | false | 只处理人类评论 |
| --bot-accounts | | 额外 bot 账号（逗号分隔） |
| --work-dir | . | 仓库目录 |
| --once | false | 只执行一次 |

---

## 脚本调用速查

| 场景 | 命令 |
|------|------|
| 验证 token | `python3 ~/.claude/skills/vibe-pr/scripts/gitcode_api.py` |
| 获取 issue 详情 | `python3 ~/.claude/skills/vibe-pr/scripts/issue_parser.py --repo <repo> --issue <n>` |
| 获取 issue（URL） | `python3 ~/.claude/skills/vibe-pr/scripts/issue_parser.py --url <issue_url>` |
| 查 PR 状态 | `python3 ~/.claude/skills/vibe-pr/scripts/pr_status.py --repo <repo> --pr <n>` |
| 更新 PR 标题/描述 | `gitcode_api.update_pull(repo, token, number, title='...', body='...')` |
| 解析 PR 评论 | `python3 ~/.claude/skills/vibe-pr/scripts/comment_parser.py --repo <repo> --pr <n>` |
| 解析最新 push 后评论 | `python3 ~/.claude/skills/vibe-pr/scripts/comment_parser.py --repo <repo> --pr <n> --since-commit` |
| 分析 reviewer | `python3 ~/.claude/skills/vibe-pr/scripts/reviewer_activity.py --repo <repo> --pr <n> --recent 30` |
| 手动指定候选人 | `python3 ~/.claude/skills/vibe-pr/scripts/reviewer_activity.py --repo <repo> --candidates "a,b,c" --recent 30` |
| 启动 PR 监控 | `python3 ~/.claude/skills/vibe-pr/scripts/pr_monitor.py --repo <repo> --pr <n>` |
| 单次监控（调试） | `python3 ~/.claude/skills/vibe-pr/scripts/pr_monitor.py --repo <repo> --pr <n> --once` |
| 读取 PR 上下文 | `python3 ~/.claude/skills/vibe-pr/scripts/task_context.py --repo <repo> --pr <n>` |
| 初始化 PR 上下文 | `python3 ~/.claude/skills/vibe-pr/scripts/task_context.py --repo <repo> --pr <n> --init` |
| 列出活跃 PR | `python3 ~/.claude/skills/vibe-pr/scripts/task_context.py --list` |

---

## 合并排障

当 PR 满足四个标签却未合并时，检查：

| 症状 | 解决方法 |
|------|---------|
| squash 冲突 | 用户需 rebase 目标分支后重新 push |
| 并发合并冲突 | 等 1 分钟后评论 `/check-pr` 重试 |
| PR 标题含 `[WIP]` | 删掉 `[WIP]` 前缀 |
| 未解决的评审意见 | 先 resolve 所有 CodeReview 讨论 |
| CI 重试按钮无效 | 重新评论 `/compile` 完全重跑（重试按钮只重跑失败任务） |

---

## Bot 命令速查

| 命令 | 功能 |
|------|------|
| `/compile` | 触发 CI |
| `/lgtm` / `/lgtm cancel` | 审查通过 / 撤销 |
| `/approve` / `/approve cancel` | 同意合并 / 撤销 |
| `/check-cla` | 重新检查 CLA |
| `/check-pr` | 检查标签并触发合并 |
| `/assign @user` | 分配 Issue |
| `/close` | 关闭 Issue |
