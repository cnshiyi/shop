# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-10 00:58 CST
- 状态：已重构服务器续费和未附加 IP 恢复续费的分流逻辑。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 前端分支：`codex/cloud-asset-list-performance`
- 目标主分支：`main`

## 本轮背景

- 用户指出重大 bug：普通服务器续费不应选择套餐并要求输入代理链接。
- 正确口径：
  - 普通服务器续费：在当前资产到期事实 `CloudAsset.actual_expires_at` 或当前时间基础上加 1 个自然月。
  - 未附加固定 IP 续费：才进入选择套餐、输入旧代理链接、支付后恢复服务器并绑定旧固定 IP。
- 复查发现旧测试和服务入口把运行中服务器也带进了 `prepare_cloud_asset_renewal_with_link()`，这会混淆普通续费和恢复续费。
- 同时测试暴露：无订单资产创建操作订单时，旧顺序会先创建一条新的 `CloudAsset`，再把原资产绑定订单，触发同 IP 重复资产风险。

## 修复内容

- `cloud/services.py`
  - 新增月度续费计算：`days=31` 的标准续费按 1 个自然月顺延，不再按 31 天硬加。
  - 普通运行中服务器续费完成后，`actual_expires_at`、关机计划、删机计划和 IP 回收计划按新的到期时间一起后移。
  - 新增未附加 IP 恢复续费候选判定：只有无订单、未附加固定 IP、且未被云端确认删除的资产才能进入选套餐和输入链接流程。
  - `prepare_cloud_asset_renewal_with_link()` 对普通运行中服务器硬拒绝，避免旧按钮或旧回调绕过。
  - 修复 `_create_asset_operation_order()`：先把原 `CloudAsset` 绑定到新操作订单，再同步到期记录，避免创建同 IP 第二条资产。
- `cloud/tests.py`
  - 将普通无订单服务器续费测试改为不再返回恢复套餐列表。
  - 新增未附加固定 IP 仍返回恢复套餐列表的测试。
  - 新增普通服务器不能走输入链接恢复流程的防绕过测试。
  - 普通钱包续费测试改为走“确保操作订单 -> 创建普通续费单 -> 钱包支付”，并断言到期时间加 1 个自然月、计划时间后移、同 IP 资产只有 1 条。
- `orders/tests.py`
  - 链上支付普通续费测试改为走普通续费单，不再通过未附加 IP 恢复入口。
  - 断言链上支付后到期时间加 1 个自然月、计划时间后移、同 IP 资产只有 1 条。

## 验证

通过：

```bash
uv run python -m py_compile cloud/services.py bot/handlers.py cloud/tests.py orders/tests.py
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_running_asset_without_order_renewal_does_not_list_recovery_plans cloud.tests.CloudServerServicesTestCase.test_unattached_ip_renewal_lists_recovery_plans_without_creating_order cloud.tests.CloudServerServicesTestCase.test_running_asset_renewal_rejects_link_recovery_flow cloud.tests.CloudServerServicesTestCase.test_prepare_unbound_asset_renewal_creates_pending_payment_order cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_wallet_payment_marks_paid_for_recovery cloud.tests.CloudServerServicesTestCase.test_active_asset_renewal_wallet_payment_extends_asset_and_lifecycle cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_wallet_payment_repairs_completed_unpaid_state cloud.tests.CloudServerServicesTestCase.test_completed_asset_recovery_order_renews_without_reprovisioning cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_chain_payment_marks_paid_for_recovery orders.tests.ChainPaymentScannerTestCase.test_active_asset_renewal_chain_payment_extends_asset_and_lifecycle --settings=shop.settings --verbosity=1
uv run python manage.py check
git diff --check
```

结果：

- 编译通过。
- 续费分流、钱包支付、链上支付聚焦测试 10 条通过。
- Django 系统检查通过。
- diff 空白检查通过。
- SQLite 聚焦测试仍输出既有 `db_comment/db_table_comment` 告警，不属于本轮问题。

## 结论

- 普通服务器续费不再选择套餐、不再要求输入代理链接。
- 未附加固定 IP 续费仍保留选择套餐和输入旧代理链接的恢复流程。
- 普通续费按 1 个自然月顺延，生命周期计划随 `CloudAsset.actual_expires_at` 后移。
- 创建操作订单不会再制造同 IP 重复资产。
