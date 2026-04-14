# PR !1675 — test(platform): test ci failure detection workflow

repo: cann/hcomm
pr: 1675
branch: test/ci-failure-demo
updated: 2026-04-11 13:40

## 当前阶段

已关闭（测试完成）

## 进度

- [x] 阶段 2：PR 已创建
- [x] 阶段 3：CLA 通过
- [x] 阶段 4：CI 第1次触发，4个编译任务全部FAILED
- [x] 阶段 4a：自动分析diff，定位typo readesNum_ → readersNum_，修复并push
- [x] 阶段 4b：CI 第2次触发，全部15个任务PASSED（ci-pipeline-passed）
- [x] PR 已关闭

## 关键决策

- 故意在 read_write_lock.cc:27 引入 typo：readersNum_ → readesNum_
- CI失败后用diff自分析策略定位和修复
- 修复了 ci_log_fetcher.py 的3个bug（URL regex、author字段、logs API ID组合）
- 确认 openLiBing CICD API projectId=300033 对应 CANN 项目
- 确认 Build 子服务日志需要浏览器认证（当前不可用）

## 额外成果：ci_log_fetcher.py 改进

测试过程中发现并修复了 ci_log_fetcher.py 的多个问题：
1. HTML表格行regex不匹配无引号URL → 兼容 href=URL 和 href="URL"
2. author字段为空dict → 改用 user 字段获取评论者
3. logs API 用了 dispatch_id → 改用 job.id + step.id
4. 新增 PROJECT_ID_MAP: CANN → 300033
5. 确认Build子服务的编译日志需要浏览器认证，调度层日志可公开访问
