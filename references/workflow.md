# CANN 社区 PR Workflow 参考

## 概述

CANN 社区（hcomm、hccl 等仓库）托管在 GitCode 上，使用 cann-robot 驱动自动化流程。合并需要四个标签全部就位：`cann-cla/yes`、`ci-pipeline-passed`、`lgtm`、`approved`。

默认合并方式为 squash merge（多个 commit 合并为一个）。

## 1. Fork + Push

不允许直接 push 到 CANN 仓库，必须从个人 fork 提 PR。

```
POST https://gitcode.com/api/v5/repos/{owner}/{repo}/forks
Header: PRIVATE-TOKEN: <token>
```

推送分支后创建 PR，head 参数格式必须为 `"用户名:分支名"`：

```
POST https://gitcode.com/api/v5/repos/{owner}/{repo}/pulls
Body: {"title": "...", "head": "fan33:fix/branch", "base": "master", "body": "..."}
```

### Commit Message 规范

```
<type>(<scope>): <subject>

<body>

<footer>
```

type 取值：feat / fix / docs / style / refactor / perf / test / chore

示例：
```
fix(hccl): fix deadlock in collective communication

The deadlock occurred when two processes tried to acquire
the same lock in different order during reduce operation.

Fixes #123
```

### 版权声明

所有新建源代码文件需在头部添加版权声明（CANN Open Software License Agreement Version 2.0）：
```cpp
/*
 * Copyright (c) <yyyy> [name of copyright owner].
 * This program is free software, you can redistribute it and/or modify it under the terms and conditions of
 * CANN Open Software License Agreement Version 2.0 (the "License").
 * Please refer to the License for details. You may not use this file except in compliance with the License.
 * THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
 * INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, OR FITNESS FOR A PARTICULAR PURPOSE.
 * See LICENSE in the root of the software repository for the full text of the License.
 */
```

## 2. CLA 检查

- PR 创建后 cann-robot 秒级自动检查
- 以 commit 的 committer email 为准（不是 author email）
- 签署页面：https://clasign.osinfra.cn/sign/68cbd4a3dbabc050b436cdd4
- 查询 API：`GET https://clasign.osinfra.cn/api/v1/individual-signing/68cbd4a3dbabc050b436cdd4?email=<email>`
  - 返回结果中必须包含两个 true 值表示成功签署

### 三种签署路径

| 类型 | 流程 | 注意事项 |
|------|------|---------|
| 个人开发者 | 在签署页面填邮箱→验证→立即生效 | 推荐个人邮箱（Gmail 等），企业邮箱在公司后续签 CCLA 时会失效 |
| 企业员工 | 用公司邮箱提交登记→联系管理员审批→审批后生效 | 必须主动联系管理员，不会自动审批 |
| 企业代表 | 提交申请→打印 PDF 盖章→邮件回复→获得管理员账号 | 整个流程需要纸质签署 |

### 邮箱修正方法
```bash
git -c user.email="cla-email@example.com" commit --amend --reset-author --no-edit
git push fork branch --force
```
然后在 PR 评论区输入 `/check-cla`。

Git 邮箱优先级：本地仓库配置 > 全局配置 > 系统配置。

## 3. 触发 CI

CI 不自动触发。在 PR 评论区输入 `compile`（或 `/compile`）。

CI 运行在华为 CodeArts 平台，耗时约 20-30 分钟。通过后自动打标签 `ci-pipeline-passed`。

每次新 push 会移除 `ci-pipeline-passed`，需重新触发。

### CI 流水线阶段

| 阶段 | 任务 | 说明 |
|------|------|------|
| 静态检查 | codecheck | 代码安全和质量检查 |
| | anti_virus | 恶意文件全量扫描 |
| | SCA | 开源引用合规性检查 |
| | Check_Pr | PR 内容合规性校验 |
| | API_Check | API 接口兼容性全量校验（WARNING 不影响合入） |
| 编译构建 | Compile_Ascend_X86 | X86 平台编译 |
| | Compile_Ascend_ARM | ARM 平台编译 |
| 测试 | UT_{xxx} | 单元测试（按模块拆分并行） |
| | ST_{xxx} | 系统集成测试 |
| | Smoke_{xxx} | 冒烟测试（按芯片型号拆分） |

### CI 任务状态

- running：执行中
- waiting：待执行（资源排队）
- FAILED：执行失败
- WARNING：执行失败但不影响合入（试运行阶段）
- SUCCESS：执行成功
- ABORTED：关联任务失败，自动中止

### CI 重试

PR 顶部"检查"处的重试按钮只重试 FAILED 任务，不等同于完全重新触发。要完全重跑，需重新评论 `/compile`。

## 4. 代码审批

按模块分区审批，每个被修改的模块需要：
- 至少 2 个 committer `/lgtm`
- 至少 1 个 committer `/approve`（隐含 /lgtm）

候选人从 cann-robot 的欢迎评论表格中获取（`You can also ask: ...`）。

审批有效性约束：reviewer 的 lgtm/approve 时间必须晚于最新 commit 时间。新 push 代码会使之前的审批失效。

### 权限模型

权限配置在 `sig-info.yaml` 中，支持多级粒度：

| 级别 | 说明 | 命令权限 |
|------|------|---------|
| SIG 级 committer | 拥有 SIG 下所有仓库权限 | /lgtm, /approve |
| 仓库级 committer | 特定仓库权限 | /lgtm, /approve |
| 分支级 committer | 特定分支权限 | /lgtm, /approve |
| 路径级 committer | 特定目录/文件权限 | /lgtm, /approve |
| maintainer | SIG 全仓库权限 | /lgtm |
| branch_keeper | 分支版本经理 | /merge |

注意：
- gitcode_id 区分大小写，必须与 GitCode 账号完全匹配
- 权限修改后需要约 10 分钟生效
- PR 作者和机器人不能触发标签命令
- 非机器人账号添加的标签无效（手动添加 lgtm 等会被忽略）

## 5. Bot 命令

| 命令 | 功能 | 谁可以用 |
|------|------|---------|
| `/compile` 或 `compile` | 触发 CI 流水线 | 所有开发者 |
| `/lgtm` | 代码审查通过 | committer / maintainer |
| `/lgtm cancel` | 撤销 lgtm | committer / maintainer |
| `/approve` | 同意合并（含 lgtm），默认 squash 合并 | committer |
| `/approve cancel` | 撤销 approve | committer |
| `/merge` | 添加 keeper_approved 标签 | branch_keeper |
| `/check-cla` | 重新检查 CLA | 所有开发者 |
| `/cla cancel` | 删除 cann-cla/yes 标签 | 仓库管理员 |
| `/check-pr` | 检查标签是否满足合并条件，满足则自动合并 | 任何人 |
| `/assign` 或 `/assign @user` | 分配 Issue | 所有人 |
| `/unassign` 或 `/unassign @user` | 取消分配 | 所有人 |
| `/close` | 关闭 Issue | 所有人 |
| `/kind <label>` | 添加类型标签（如 kind/bug） | 所有人 |
| `/remove-kind <label>` | 移除类型标签 | 所有人 |
| `/priority <level>` | 添加优先级标签 | 所有人 |
| `/sig <sig-name>` | 添加 SIG 标签 | 所有人 |

## 6. 标签系统

| 标签 | 含义 | 如何产生 |
|------|------|---------|
| `cann-cla/yes` | CLA 已签署 | cann-robot 自动检查 |
| `cann-cla/no` | CLA 未签署 | cann-robot 自动检查 |
| `ci-pipeline-passed` | CI 通过 | CI 流水线完成后自动添加 |
| `ci-pipeline-failed` | CI 失败 | CI 流水线完成后自动添加 |
| `api_check_failed` | API 兼容性检查失败 | WARNING 状态，不影响合入 |
| `lgtm` | 代码审查通过 | committer 评论 /lgtm |
| `approved` | 合并授权通过 | committer 评论 /approve |
| `keeper_approved` | 版本经理批准 | branch_keeper 评论 /merge |

## 7. 自动合并

以下标签全部就位时，评论 `/check-pr` 触发 cann-robot 自动执行 squash merge：

| 标签 | 说明 |
|------|------|
| `cann-cla/yes` | CLA 签名通过 |
| `lgtm` | 所有模块代码审查通过 |
| `approved` | 所有模块合并授权通过 |
| `ci-pipeline-passed` | CI 流水线通过 |

### 合并失败排查

| 症状 | 原因 | 解决方法 |
|------|------|---------|
| fast-forward 失败 | PR commits 非基于目标分支最新 commit | 对目标分支 rebase，或管理员修改 PR 合并模式 |
| squash 失败 | PR 代码与目标分支存在冲突 | 解决冲突后重新 push |
| 并发合并冲突 | 同时间同分支另一 PR 正在合并 | 等待 1 分钟后重新评论 `/check-pr` |
| 机器人权限不足 | cann-robot 无该分支合并权限 | 管理员授予机器人推送和合并权限 |
| WIP 标记阻止 | PR 标题以 `[WIP]` 开头 | 删除标题中的 `[WIP]` |
| 评审意见未解决 | PR 存在未解决的 CodeReview 讨论 | 先解决所有评审意见 |
| 标签不完整 | 缺少必要标签 | 检查 committer 权限配置、commit 时间、邮箱配置 |

## 8. hccl SIG 信息

### 管理的仓库

| 仓库 | 定位 |
|------|------|
| hixl | 底层通信基础库 |
| hccl | 集合通信库 |
| hcomm | 点对点和通信框架 |

### 核心成员

Maintainer（3 人）：yanyefeng（颜业峰）、wenxuemin（文学敏）、leabclove（程祥乐）

Committer：20+ 人，包括严正行、殷鼎、李连林等

### 会议

- 频次：两周一次，单周周五 14:00-16:00（北京时间）
- 议题申报：https://etherpad-cann.meeting.osinfra.cn/p/sig-hccl
- 邮件列表：hccl@cann.osinfra.cn
- 会议地址：https://meeting.osinfra.cn/cann/

### 权限配置特点

hccl SIG 采用分层权限管理，在 sig-info.yaml 中对三个仓库分别配置 committer，并对 `include`、`src/python`、`test/st/algorithm` 等路径设置路径级权限。

sig-info 配置：https://gitcode.com/cann/community/blob/master/CANN/sigs/hccl/sig-info.yaml

## 9. 角色晋升路径

| 角色 | 获得方式 | 条件 |
|------|---------|------|
| Contributor | 提交代码即自动获得 | 无 |
| Committer | 仓库级申请，SIG 投票 | 3 个月以上贡献、10+ PR 审核、10+ 实质性 PR、4 票赞成无反对 |
| Maintainer | SIG 投票或新建 SIG | 现任 Maintainer 提名 + SIG 投票 1/2+ 赞成 |

非活跃退出：Committer 连续 6+ 个月无活动、Maintainer 连续 3+ 个月无会议参与。

## 10. 常见踩坑

1. API 认证必须用 `PRIVATE-TOKEN` header，不是 `access_token` 参数
2. CLA 邮箱修正必须 `--reset-author` 才能同时改 committer email
3. Fork PR head 参数必须是 `"用户名:分支名"` 格式
4. CLA 签署只能在浏览器完成，没有纯 API 方式
5. gitcode_id 区分大小写，权限修改后约 10 分钟生效
6. PR 标题包含 `[WIP]` 会阻止自动合并
7. 未解决的 CodeReview 讨论会阻止合并
8. CI 重试按钮只重试失败任务，不等同于重新触发 `/compile`

## 11. 基础设施支撑联系方式

| 服务 | 联系人 | 邮箱 |
|------|--------|------|
| 新建流水线 | @tanghaoran7 | tanghaoran7@huawei.com |
| CANN-robot | @Coopermassaki | fuyong29@h-partners.com |
| CLA 签署 | @yajie_caroline | chenyajie6@huawei.com |
| 邮件列表 | @weixin_43493709 | zhuchao50@h-partners.com |
| 会议服务 | @ZeesangPie | chenglang11@huawei.com |
| 漏洞管理 | @yangwei999 | yangwei266@h-partners.com |
| 数字化平台 | @fanxiaotian6 | fanxiaotian6@huawei.com |

## 12. 参考链接

- sig-info 配置：https://gitcode.com/cann/community/blob/master/CANN/sigs/hccl/sig-info.yaml
- Bot 使用指南：https://gitcode.com/cann/infrastructure/blob/main/docs/robot/robot使用指南.md
- CI 指导文档：https://gitcode.com/cann/infrastructure/blob/main/docs/ci/ci_guide.md
- CLA 签署：https://clasign.osinfra.cn/sign/68cbd4a3dbabc050b436cdd4
- 社区会议：https://meeting.osinfra.cn/cann/
- hccl SIG 会议纪要：https://etherpad-cann.meeting.osinfra.cn/p/sig-hccl
- 邮件列表订阅：https://mailweb.cann.osinfra.cn/mailman3/lists/
- 数字化协作平台：https://digital.hicann.cn/
- C++ 编码规范：参见 community 仓库 contributor/coding-standards/ 目录
