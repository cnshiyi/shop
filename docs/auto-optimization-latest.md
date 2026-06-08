# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 22:35 CST
- 状态：修复运行中人工同步资产续费钱包支付后不延长 `CloudAsset.actual_expires_at` 的问题。
- 后端提交：本轮代码与记录待提交。
- 前端提交：本轮无前端变更。

## 本轮覆盖范围

- 后端仓库：`/Users/a399/Desktop/data/shop`
- 重点链路：
  - 未绑定/人工同步资产续费
  - 钱包支付续费
  - 运行中 AWS Lightsail 同步资产
  - `CloudAsset.actual_expires_at` 到期事实
  - 续费后的关机、删机、IP 回收计划派生时间

## 本轮发现

- 真机测试暴露：运行中的人工同步服务器生成续费单并手工改为 `paid` 后，资产到期事实不会延长。
- 代码巡检继续发现未提交的续费修复草稿存在缩进错误，导致 `cloud/services.py` 无法编译。
- 续费钱包支付逻辑原先把“未绑定代理资产续费”统一当作恢复固定 IP 流程；对于仍有 `instance_id` 且运行中的同步资产，支付后只停留在 `paid`，不会把资产续期到未来。

## 本轮修复

- 新增 `_asset_renewal_active_server_asset()`，识别“未绑定资产续费”里仍处于运行中的真实服务器资产。
- `pay_cloud_server_renewal_with_balance()` 在钱包支付成功后：
  - 若续费对象是运行中资产，直接按当前资产到期事实或当前时间顺延 `days`。
  - 更新订单生命周期字段和状态为 `completed`。
  - 更新 `CloudAsset.actual_expires_at`、`status=running`、`is_active=True` 和价格。
  - 写入续费 IP 日志并刷新计划快照。
  - 若不是运行中资产，保留原固定 IP 恢复流程，继续标记为 `paid`。
- 修复续费钱包支付函数的缩进错误，保证支付、扣款、状态更新仍在事务块内。
- 新增回归测试：运行中同步资产续费钱包支付后，资产到期事实、订单到期事实和生命周期计划一起后移。
- 为相邻续费测试补充云端固定 IP 反查 mock，避免 SQLite 聚焦测试访问真实 AWS。

## 本轮验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/services.py cloud/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_address_order_forces_usdt_from_trx_source cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_rejects_link_port_override cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_wallet_payment_marks_paid_for_recovery cloud.tests.CloudServerServicesTestCase.test_active_asset_renewal_wallet_payment_extends_asset_and_lifecycle --settings=shop.settings --verbosity=1
git diff --check
```

说明：

- SQLite 聚焦测试仍输出既有 `db_comment/db_table_comment` 能力差异告警，不属于本轮问题。
- 本轮未执行真实支付、链上广播、真实云资源创建、真实关机、真实删机、IP 释放或生产发布。

## 结论

- 运行中的人工同步服务器走钱包续费时，支付成功后会立即延长 `CloudAsset.actual_expires_at`，并让订单生命周期计划随之后移。
- 固定 IP 保留/恢复类续费仍保留原 `paid` 后续恢复流程。
- 本轮修复直接覆盖刚才真机测试暴露的 `paid` 但不续期风险。

## 剩余风险

- 地址支付链路的链上确认路径仍需继续复核是否也能覆盖运行中资产续期。
- 计划页对 `paid` 订单的展示与执行器 due 队列口径差异仍可继续专项检查。
- Telegram 真机交互压测仍受 Bot API / MTProto 网络可达性影响。
