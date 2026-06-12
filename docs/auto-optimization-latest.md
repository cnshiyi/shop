# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-12 21:42 CST
- 状态：已修复自动续费失败后用户手动续费，后台自动续费计划仍重复显示待执行/失败的问题。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 目标主分支：`main`

## 本轮背景

- 用户反馈：自动续费进入待执行后执行失败，用户随后手动续费成功，但自动续费仍继续重复显示待执行。
- 本轮属于用户明确点名自动续费逻辑，因此允许最小修改自动续费状态闭环；未改生命周期执行器、计划页其它规则或代理列表展示。

## 修改摘要

- `cloud/services.py`
  - 新增续费成功后的自动续费失败结清逻辑。
  - 手动续费或用户钱包续费成功后，会取消同订单仍处于 `pending/failed` 的自动续费重试任务。
  - 如果同订单近 7 天存在自动续费失败日志，会补一条 `manual-renew-resolved-*` 成功巡检日志，用于覆盖旧失败状态。
  - 自动续费执行器自身调用钱包续费时不写额外手动解决日志，继续由原自动续费成功巡检日志记录结果，避免重复历史。
- `cloud/task_center.py`
  - 后台任务中心统计最近自动续费失败历史时，若同订单失败后已有成功日志，则不再把旧失败计入红色失败项。
- `cloud/tests.py`
  - 新增回归测试覆盖“自动续费失败 -> 手动续费成功 -> 旧重试取消 -> 计划页不再重复待执行 -> 任务中心不再显示旧失败”。

## 验证命令

通过：

```bash
uv run python -m py_compile cloud/services.py cloud/lifecycle.py cloud/task_center.py cloud/tests.py
uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_manual_renewal_settles_auto_renew_failure_queue cloud.tests.CloudServerServicesTestCase.test_auto_renew_prefers_bound_normal_asset_user_over_admin_order_user cloud.tests.CloudServerServicesTestCase.test_auto_renew_retry_task_waits_for_recharge_then_retries cloud.tests.CloudServerServicesTestCase.test_auto_renew_task_detail_includes_due_retry_and_fallback_items cloud.tests_task_center.CloudTaskCenterApiTestCase.test_auto_renew_section_counts_recent_failed_history_as_failed --keepdb --noinput --verbosity 2
uv run python manage.py check
git diff --check
```

## 风险和下一步

- 本轮只修改自动续费失败状态结清和任务中心展示统计，不执行真实支付、链上广播、真实云资源操作、生产发布或删除数据。
- 自动续费失败历史不会被删除，只通过后续成功日志和重试任务取消状态让计划/任务中心不再重复报警。
