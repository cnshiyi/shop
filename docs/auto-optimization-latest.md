# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-11 01:32 CST
- 状态：已调整生命周期计划表排序，让同一到期时间内的同一用户记录相邻显示。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 前端分支：未改动
- 目标主分支：`main`

## 本轮背景

- 用户反馈计划表显示问题：同一用户、同一到期时间的记录希望相邻展示。
- 这是用户明确点名计划表显示调整，因此允许修改计划相关查询；未修改代理列表。

## 修改内容

- `cloud/lifecycle_plan_queries.py`
  - 新增计划表共享排序 `LIFECYCLE_PLAN_GROUPED_ORDERING = ('actual_expires_at', 'user_id', 'id')`。
  - 关机计划、删机计划按“到期时间 -> 用户 -> 资产 ID”排序。
  - 未附加 IP 删除计划同样按“到期时间 -> 用户 -> 资产 ID”排序。
  - 深分页尾页优化的倒序排序同步改为“到期时间倒序 -> 用户倒序 -> 资产 ID 倒序”，保持前后页排序一致。
- `cloud/tests.py`
  - 新增两个回归测试，分别覆盖服务器生命周期计划和未附加 IP 删除计划在同一到期时间下按用户聚合。

## 验证

通过：

```bash
git diff --check
uv run python -m py_compile cloud/lifecycle_plan_queries.py cloud/tests.py
uv run python manage.py check
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_server_lifecycle_plan_groups_same_user_with_same_expiry cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_plan_groups_same_user_with_same_expiry --keepdb --noinput --verbosity 1
```

## 风险和下一步

- 本轮只改变计划表返回顺序，不改变生命周期执行条件、执行时间、状态计算或资产到期事实。
- 历史列表仍保持按执行/更新时间倒序，不强行按用户聚合，避免破坏历史时间线。
