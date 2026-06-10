# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-11 01:43 CST
- 状态：已修复计划页搜索只覆盖数据库字段、无法命中装饰后展示列的问题。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 前端分支：未改动；已核对计划页会把 `keyword` 传给 `/admin/tasks/plans/`。
- 目标主分支：`main`

## 本轮背景

- 用户反馈计划页搜索不生效，要求可以搜索所有列、正确返回结果，并支持模糊匹配。
- 本轮属于用户明确点名计划页搜索，因此允许修改计划页接口；未修改代理列表。

## 修改内容

- `bot/api.py`
  - 计划页带 `keyword` 时，先按当前完整候选集生成展示行并执行装饰，再对装饰后的接口字段做全文模糊过滤。
  - 搜索范围覆盖 `status_summary`、`plan_state_label`、`queue_status_label`、`execution_status`、日期、布尔开关展示含义等接口返回列。
  - 单个关键词保持子串匹配；多个空格分隔词按“每个词可命中任意列”的方式匹配，支持跨列组合搜索。
  - 过滤后重新计算各表总数，并按过滤结果分页，避免返回未过滤总数或错页。
- `cloud/tests.py`
  - 新增执行状态等装饰列搜索回归测试，验证即使 `fields=basic` 隐藏执行列，搜索仍能命中。
  - 新增日期列模糊搜索回归测试。

## 实测

- 已用接口请求创建临时测试资产并回滚，搜索 `tmp-plan-search-execution-hit 资产关机计划开关关闭` 返回：
  - HTTP 状态：`200`
  - 命中：`True`
  - `shutdown_plan_count`：`1`
  - 当前页加载：`1`

## 验证

通过：

```bash
git diff --check
uv run python -m py_compile bot/api.py cloud/tests.py
uv run python manage.py check
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_keyword_matches_decorated_execution_columns cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_keyword_matches_date_columns --keepdb --noinput --verbosity 1
```

## 风险和下一步

- 本轮只改计划页查询接口的搜索过滤方式，不改变生命周期执行条件、执行时间、真实状态计算或 `CloudAsset.actual_expires_at` 到期事实。
- 前端计划页已存在搜索入口并传递 `keyword`，本轮未修改前端页面。
