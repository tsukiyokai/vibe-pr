# GitCode CANN/HCOMM仓库开发Workflow备忘

> 基于2026-02-25实际提交PR #584的经验整理

## 仓库信息

- 地址：https://gitcode.com/cann/hcomm
- 平台：GitCode（基于GitLab，API风格混合GitLab + Gitee）
- 主分支：master
- 合并方式：squash merge（多commit时自动标记 `stat/needs-squash`）

## PR完整生命周期

```
创建 PR → CLA 检查 → 手动触发 CI → CI 流水线执行 → 人工 Review（/lgtm + /approve）→ 自动合并
```

## 一、Fork + Push流程

CANN组织仓库不允许直接push，必须fork后从个人仓库提PR。

```bash
# 1. Fork（通过 API）
curl -X POST "https://gitcode.com/api/v5/repos/cann/hcomm/forks" \
  -H "PRIVATE-TOKEN: $TOKEN"

# 2. 添加 fork remote
git remote add fork https://gitcode.com/fan33/hcomm.git

# 3. 推送分支
git push -u fork fix/your-branch-name

# 4. 通过 API 创建 PR（注意 head 格式为 "用户名:分支名"）
curl -X POST "https://gitcode.com/api/v5/repos/cann/hcomm/pulls" \
  -H "PRIVATE-TOKEN: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"...","head":"fan33:fix/your-branch","base":"master","body":"..."}'
```

GitCode API认证方式：使用 `PRIVATE-TOKEN` header，不是 `access_token` 参数。

## 二、CLA签署

PR创建后cann-robot秒级自动检查CLA状态。

检查机制：以commit的committer email为准（不是author email）。

签署CLA时使用的邮箱必须和git commit的邮箱一致。如果不一致，需要amend commit：

```bash
# 同时重置 author 和 committer 邮箱
git -c user.email="your-cla-email@example.com" commit --amend --reset-author --no-edit
git push fork your-branch --force
```

重新推送后在PR评论 `/check-cla` 触发重新检查。

CLA签署状态查询API：
```
GET https://clasign.osinfra.cn/api/v1/individual-signing/68cbd4a3dbabc050b436cdd4?email=your-email
```
返回 `signed: true` 且 `version_matched: true` 表示通过。

个人CLA签署页面（需浏览器操作，无纯API方式）：
https://clasign.osinfra.cn/sign-cla/68cbd4a3dbabc050b436cdd4/individual

## 三、CI流水线

CI不自动触发，需要在PR评论区手动输入 `compile` 或 `/compile`。

CI在华为CodeArts平台(openlibing.com)运行，包含以下任务：

| 任务               | 说明                               |
| ------------------ | ---------------------------------- |
| codecheck          | 静态代码检查                       |
| SCA                | 软件成分分析                       |
| anti_virus         | 防病毒扫描                         |
| Check_Pr           | PR规范检查                         |
| Compile_Ascend_X86 | x86架构编译                        |
| Compile_Ascend_ARM | ARM(aarch64)架构编译               |
| API_Check          | API兼容性检查（WARNING不阻塞合并） |
| pre_comment        | 前置评论检查                       |
| UT_Test            | 单元测试（含覆盖率报告）           |
| ST_Test            | 系统测试                           |
| Smoke_A900         | 昇腾A900硬件冒烟测试               |

执行时间约20-30分钟。全部通过后自动打标签 `ci-pipeline-passed` + `api-check-pass`。

运行中标签为 `ci-pipeline-running`。

流水线链接格式：`https://www.openlibing.com/apps/pipelineDetail?pipelineId=XXX&pipelineRunId=XXX&projectName=CANN`

每次新push代码会自动移除 `ci-pipeline-passed` 标签，需要重新评论 `compile` 触发。

## 四、代码审批机制

审批按模块分区进行，模块与reviewer的映射定义在：
- sig-info配置：https://gitcode.com/cann/community/blob/master/CANN/sigs/hccl/sig-info.yaml
- bot命令文档：https://gitcode.com/cann/infrastructure/blob/main/docs/robot/robot使用指南.md
- CI指导文档：https://gitcode.com/cann/infrastructure/blob/main/docs/ci/ci_guide.md

每个被修改的模块需要满足：
- 至少2个committer评论 `/lgtm`（代码审查通过）
- 至少1个committer评论 `/approve`（同意合并，隐含 /lgtm）

cann-robot的欢迎评论会实时更新模块审批表格，并列出可以找谁review：

```
| module | lgtm status | approve status |
|--------|-------------|----------------|
| src    | ❌ (0/2)(You can also ask: linyf950, ...) | ❌ (0/1)(You can also ask: yanyefeng, ...) |
```

hcomm `src` 模块的reviewer（PR #584实测）：
- lgtm可找：linyf950、lilin_137、chenke2026、leabclove、chengym
- approve可找：abiggg、chenke2026、leabclove、yanyefeng、ouyangxizi

Reviewer的lgtm/approve时间必须晚于最新commit时间，否则无效。
即：新push代码会使之前的审批失效。

## 五、自动合并条件

以下标签全部就位时cann-robot自动执行合并：
- `cann-cla/yes` — CLA签名通过
- `lgtm` — 所有模块代码审查通过
- `approved` — 所有模块合并授权通过
- `ci-pipeline-passed` — CI流水线通过

## 六、Bot命令速查

| 命令                   | 功能               | 谁可以用       |
| ---------------------- | ------------------ | -------------- |
| `compile` / `/compile` | 触发CI流水线       | 所有开发者     |
| `/lgtm`                | 标记代码审查通过   | sig组committer |
| `/lgtm cancel`         | 撤销lgtm           | sig组committer |
| `/approve`             | 同意合并（含lgtm） | sig组committer |
| `/approve cancel`      | 撤销approve        | sig组committer |
| `/check-cla`           | 重新检查CLA        | 所有开发者     |
| `/check-pr`            | 检查标签并触发合并 | 任何人         |

## 七、典型PR时间线参考

PR #581（他人，从创建到合并52分钟）：
```
17:17  PR 创建
17:17  bot 欢迎消息 + CLA 通过
17:45  committer /approve
17:46  committer /lgtm
17:48  作者评论 compile
17:48  bot 确认流水线触发
18:09  CI 全部通过（约 21 分钟）
18:09  bot 自动合并
```

PR #584（我们的实际经历）：
```
23:25  PR 创建（fork → push → API 创建）
23:25  bot 欢迎消息 + CLA 失败（commit 邮箱不匹配）
23:34  amend commit 修正邮箱 + force push + /check-cla → CLA 通过
23:57  评论 compile 触发 CI
~00:27 CI 全部通过（约 30 分钟），标签：ci-pipeline-passed + api-check-pass
       等待 committer review（/lgtm x2 + /approve x1）
```

## 八、踩坑记录

1. GitCode API认证：必须用 `PRIVATE-TOKEN` header，用 `access_token` 参数会报 `Invalid header parameter: private-token, required`。
2. CLA邮箱：`git -c user.email` + `--reset-author` 才能同时改author和committer，单独 `--author` 只改author，committer不变会导致CLA检查失败。
3. Fork PR的head参数格式：`"用户名:分支名"`，不是单独的分支名。
4. CLA签署只能在浏览器完成（SPA页面 + 邮箱验证码），没有纯API方式。

---

## 九、行业调研：AI驱动的自动化开发Bot生态

> 2026-02-26调研整理

### 9.1 端到端AI开发Agent

| 项目                        | 定位                 | 关键数据                                 | 启示                                               |
| --------------------------- | -------------------- | ---------------------------------------- | -------------------------------------------------- |
| Devin (Cognition)           | 端到端AI软件工程师   | PR合并率67%，三分之一仍需人工            | 100%自动化不现实，设计时必须预留人工介入路径       |
| GitHub Copilot Coding Agent | issue → draft PR     | 绑定GitHub Actions，只出draft不直接merge | "draft PR" 策略降低风险——先让人看再决定            |
| OpenAI Codex (CLI)          | 终端内AI开发助手     | 沙箱执行，支持多任务并行                 | 沙箱隔离 + 并行是agent架构的标配                   |
| OpenHands Resolver          | label触发自动修复    | 给issue打 `fix-me` 标签即触发            | label-as-trigger模式优雅，可借鉴用于CANN issue分诊 |
| mini-swe-agent              | 100行代码的SWE agent | Claude 4.5在SWE-bench上75.4%             | 说明简单agent + 强模型已经很有效，不需要过度工程   |

### 9.2 AI Code Review

| 项目                       | 覆盖规模              | 关键发现                                                                                                      |
| -------------------------- | --------------------- | ------------------------------------------------------------------------------------------------------------- |
| CodeRabbit                 | 200万仓库、1300万PR   | AI review最有效的场景是"执行团队约定的规范"（命名、格式、安全模式），不是"找bug"                              |
| Qodo (CodiumAI)            | PR评论区 /command模式 | reviewer也能指挥bot（`/improve`、`/describe`、`/ask`），不仅是PR作者                                          |
| codereview skill（我们的） | CANN C++ 规范         | 1124行编码规范、12个HCCL高频缺陷模式、5步审查方法论、置信度分级。Claude本身就是执行引擎，不需要额外Python脚本 |

### 9.3 CI/CD自动修复

| 项目                | 做法                                                           | 启示                                                                           |
| ------------------- | -------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| Meta SapFix         | CI失败后生成多个修复候选（模板修复、回滚、AI生成），用测试筛选 | "多策略尝试"值得借鉴：编译错误自动修、静态检查对照规则改、测试失败看是不是回归 |
| Google OSS-Fuzz     | 持续fuzzing，发现bug自动提issue，90天不修公开漏洞              | 自动化发现 + 社区压力机制                                                      |
| Dependabot/Renovate | 依赖更新自动PR                                                 | 做最无聊但最安全的事，成功率接近100%，赢得信任后才获准自动merge                |

### 9.4 社区Bot / ChatOps

| 项目            | 社区 | 做法                                                                                    |
| --------------- | ---- | --------------------------------------------------------------------------------------- |
| Kubernetes Prow | CNCF | 评论区命令驱动（`/lgtm`、`/approve`、`/assign`），ChatOps模式的标杆                     |
| Rust bors-ng    | Rust | merge queue：PR review通过后排队，bot逐个rebase + CI测试后合并，保证master永远绿        |
| Rust highfive   | Rust | 从贡献者历史PR学习谁擅长什么领域，自动分配reviewer                                      |
| cann-robot      | CANN | 已有ChatOps模式（`/compile`、`/lgtm`、`/approve`、`/check-pr`），我们在它之上叠加自动化 |

### 9.5 行业核心教训

1. 渐进信任路线：review（只读）→ suggest（提建议）→ fix（自动改、人批准）→ auto（全自动、事后通知）。所有成功的AI工具都走了这条路。
2. Devin的67%告诉我们：即使最先进的AI，三分之一的PR仍需人工。设计时不能假设100%成功率。
3. mini-swe-agent的75.4%告诉我们：简单agent + 强模型 > 复杂agent + 弱模型。不要过度工程。
4. CodeRabbit的1300万PR告诉我们：AI review的价值在于"执行规范"，不在于"发现bug"。规范检查是机械性的，正好适合自动化。
5. Dependabot的经验：先做最安全、最无聊的事，用100%成功率赢得信任。信任是渐进积累的。

### 9.6 codereview skill深度分析

codereview skill位于 `~/.claude/skills/codereview/`，结构：
- `SKILL.md`：5步审查流程，包含12个HCCL高频缺陷模式
- `coding-standards.md`：1124行完整编码规范（公司级 + 产品线级 + 项目级 + 部门红线 + 个人习惯）
- `googlec.md`：Google C++ Style Guide参考
- 无scripts/ 目录，无references/ 目录

关键能力：
- 9类场景的工具验证（指针赋值、算术运算、sizeof、函数返回指针、跨文件遗漏等）
- 置信度分级：确定（机械匹配）/ 较确定（已读代码验证）/ 待确认（需业务判定）
- 分层检查：严重（内存分配未判空、安全函数返回值）→ 一般（命名违规、转换问题）→ 建议（注释风格、魔鬼数字）

结论：自检不需要额外的 `self_review.py`。codereview skill + Claude = 完整的检视引擎。bot写完代码后，对diff调用codereview skill即可。
