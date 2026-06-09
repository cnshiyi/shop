# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-10 02:07 CST
- 状态：已修复自动续费执行器找不到绑定用户的问题。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 前端分支：`codex/cloud-asset-list-performance`
- 目标主分支：`main`

## 本轮背景

- 用户反馈自动续费执行失败：计划页能显示绑定用户和余额，但执行器返回“未找到可用于自动续费的绑定用户”。
- 复查发现自动续费候选人规则会排除管理员通知账号；当资产/订单只绑定管理员账号时，会被过滤到没有候选人。
- 用户明确规则：资产没有绑定普通用户、只绑定管理时，才允许资产/订单绑定管理员账号作为兜底扣款人。

## 修复内容

- `cloud/lifecycle.py`
  - 自动续费候选人同时收集订单绑定用户和主资产绑定用户。
  - 普通绑定用户和同群普通用户仍优先作为扣款候选人。
  - 管理员通知账号默认继续排除。
  - 只有没有普通候选人，且资产/订单绑定管理员去重后只有 1 个时，才允许该管理员作为兜底扣款人。
  - 执行器保留从计划资产回填订单绑定用户的保护逻辑，避免历史异常数据再次丢失绑定用户。
- `cloud/tests.py`
  - 覆盖订单用户是管理员、资产绑定普通用户时，必须扣资产普通用户。
  - 覆盖多个绑定管理员时不能任选管理员兜底。
  - 覆盖只有一个绑定管理员且没有普通用户时，允许管理员兜底扣款。
  - 保留同群普通成员余额兜底、管理员排除、自动续费重试等回归测试。

## 验证

通过：

```bash
uv run python -m py_compile cloud/lifecycle.py cloud/tests.py
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_auto_renew_candidates_exclude_admin_notice_users cloud.tests.CloudServerServicesTestCase.test_auto_renew_candidates_exclude_primary_admin_user cloud.tests.CloudServerServicesTestCase.test_auto_renew_prefers_bound_normal_asset_user_over_admin_order_user cloud.tests.CloudServerServicesTestCase.test_auto_renew_bound_admin_fallback_requires_single_bound_admin cloud.tests.CloudServerServicesTestCase.test_auto_renew_bound_admin_user_is_fallback_when_no_other_candidate cloud.tests.CloudServerServicesTestCase.test_auto_renew_group_member_can_pay_when_owner_balance_insufficient cloud.tests.CloudServerServicesTestCase.test_auto_renew_retry_task_waits_for_recharge_then_retries --settings=shop.settings --verbosity=1
uv run python manage.py check
git diff --check
```

结果：

- 编译通过。
- 自动续费候选人和扣款聚焦测试 7 条通过。
- Django 系统检查通过。
- diff 空白检查通过。
- SQLite 聚焦测试仍输出既有 `db_comment/db_table_comment` 告警，不属于本轮问题。

## 结论

- 自动续费执行器不会再因为绑定用户是唯一管理员账号而直接失败。
- 只要资产绑定了普通用户，就优先扣普通用户；管理员不会抢扣。
- 多个管理员绑定时不会任选一个扣款，仍会返回无可用绑定用户，避免扣款归属不确定。
