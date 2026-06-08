# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 22:42 CST
- 状态：修复运行中人工同步资产链上地址支付续费后不延长 `CloudAsset.actual_expires_at` 的问题。
- 后端提交：本轮代码与记录待提交。
- 前端提交：本轮无前端变更。

## 本轮覆盖范围

- 后端仓库：`/Users/a399/Desktop/data/shop`
- 重点链路：
  - 链上地址支付扫描器
  - 未绑定/人工同步资产续费
  - 运行中 AWS Lightsail 同步资产
  - `CloudAsset.actual_expires_at` 到期事实
  - 续费成功通知和续费后巡检调度

## 本轮发现

- 上一轮已修复钱包支付路径，但链上地址支付确认仍在 `orders/payment_scanner.py` 中单独处理。
- 对“未绑定代理资产续费”订单，链上确认原先只写入 `tx_hash/paid_at` 并把订单标记为 `paid`，随后进入固定 IP 恢复流程。
- 对仍有 `instance_id`、仍 `running/is_active=True` 的同步服务器资产，这会导致地址支付成功后不延长资产到期事实，关机/删机计划仍可能停在旧到期周期。
- 聚焦测试还暴露：链上续费成功通知在 async 上下文里直接调用 `order_asset_expiry()`，可能触发同步 ORM 查询并抛出 `SynchronousOnlyOperation`。

## 本轮修复

- 在 `cloud.services` 抽出 `complete_active_asset_renewal_order()`：
  - 统一识别运行中的人工同步服务器资产续费；
  - 按资产当前到期时间或当前时间顺延续费天数；
  - 同步更新订单生命周期字段、订单状态、`CloudAsset.actual_expires_at`、资产运行状态和续费日志；
  - 刷新后台计划快照。
- 钱包支付续费分支改为复用该服务函数，减少两条支付路径行为分叉。
- 链上地址支付确认分支在发现运行中资产续费时，直接完成续期并返回 `completed`，固定 IP 保留/恢复类续费仍保留 `paid` 后续恢复流程。
- 支付扫描器在同步确认阶段预读资产到期时间，并把值挂到返回对象上；异步通知只读取该缓存值，避免 event loop 内同步 ORM 查询。
- 新增回归测试：运行中同步资产通过链上地址支付续费后，订单、资产到期事实和关机/删机/IP 回收计划一起后移。

## 本轮验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/services.py orders/payment_scanner.py orders/tests.py cloud/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test orders.tests.ChainPaymentScannerTestCase.test_cloud_chain_payment_auto_submits_default_port_provision orders.tests.ChainPaymentScannerTestCase.test_active_asset_renewal_chain_payment_extends_asset_and_lifecycle cloud.tests.CloudServerServicesTestCase.test_active_asset_renewal_wallet_payment_extends_asset_and_lifecycle --settings=shop.settings --verbosity=1
git diff --check
```

说明：

- SQLite 聚焦测试仍输出既有 `db_comment/db_table_comment` 能力差异告警，不属于本轮问题。
- 本轮未执行真实支付、链上广播、真实云资源创建、真实关机、真实删机、IP 释放、生产发布或删除业务数据。

## 结论

- 运行中的人工同步服务器续费现在在钱包支付和链上地址支付两条路径上都会延长 `CloudAsset.actual_expires_at`。
- 续费成功后订单生命周期计划会随资产到期事实后移。
- 固定 IP 保留/恢复类续费仍按原流程进入 `paid` 和恢复任务，不受本轮改动影响。

## 剩余风险

- 仍需继续复核计划页 `paid` 展示口径与执行器 due 队列口径差异。
- Telegram 真机交互压测仍受 Bot API / MTProto 网络可达性影响。
