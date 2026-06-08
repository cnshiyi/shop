# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 22:45 CST
- 状态：完成真实 AWS Lightsail 创建、同步、过期、余额续费、前端页面核查和清理；修复运行中同步资产续费后不延长到期和生命周期计划的问题。
- 后端提交：代码修复已提交为 `edd14a1`、`d9a0ac6`；本轮真机报告记录待提交。
- 前端提交：无前端代码变更。

## 真机测试范围

- 测试库：`shop_manual_20260608_5676`
- 前端：`127.0.0.1:5676`
- 后端：`127.0.0.1:8010`
- 云厂商：AWS Lightsail，新加坡区。
- 云账号：后台云账号 `#55`。
- 新建资源：1 台测试服务器，实例名和公网 IP 已在真机报告中脱敏。

## 测试结论

- 真实创建服务器成功，并通过 `sync_aws_assets --region ap-southeast-1 --account-id 55` 同步为 `CloudAsset #6`。
- 人工把资产改为已过期后，生成续费订单成功。
- 修复前实测余额支付会把订单置为 `paid` 并扣款，但 `CloudAsset.actual_expires_at` 不变，关机计划仍处于到期状态。
- 修复后重跑同一订单：
  - 订单状态变为 `completed`。
  - 用户余额从 `1000` 扣到 `981`。
  - `CloudAsset.actual_expires_at` 延长到未来。
  - 订单派生的 `suspend_at`、`delete_at`、`ip_recycle_at` 全部按新到期时间后移。
  - 计划页显示关机计划为未来排期，删除计划为 `0`，没有提前进入删机执行段。
- 前端实际打开代理详情页，确认资产状态、到期时间、订单状态和生命周期日志显示正常。
- 前端实际打开计划页，确认关机计划、删除计划、IP 删除计划显示与数据库口径一致。
- 本轮新建的 AWS 测试服务器已真实删除；同步后资产进入云上不存在确认流程，当前可见代理数已减少。

## 修复内容

- `cloud/services.py`
  - 抽出运行中同步资产续费完成逻辑。
  - 钱包续费成功后，运行中资产直接完成续期，更新资产到期事实和订单生命周期计划。
  - 未附加固定 IP 恢复类订单仍保持 `paid`，等待恢复创建。
- `orders/payment_scanner.py`
  - 链上支付确认路径复用同一运行中资产续期完成逻辑。
  - 避免异步通知阶段同步查询资产到期时间。
- `orders/tests.py`
  - 增加链上支付确认后延长资产到期和生命周期计划的回归测试。

## 验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_wallet_payment_marks_paid_for_recovery cloud.tests.CloudServerServicesTestCase.test_active_asset_renewal_wallet_payment_extends_asset_and_lifecycle cloud.tests.CloudServerServicesTestCase.test_unbound_asset_renewal_wallet_payment_repairs_completed_unpaid_state orders.tests.ChainPaymentScannerTestCase.test_active_asset_renewal_chain_payment_extends_asset_and_lifecycle --keepdb
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/services.py orders/payment_scanner.py orders/tests.py
git diff --check
```

前端核查：

- `http://127.0.0.1:5676/admin/cloud-assets/6`
- `http://127.0.0.1:5676/admin/tasks/plans`

说明：

- 前端控制台仅有 Vite dev WebSocket 热更新握手错误，不是业务接口错误。
- 本轮没有真实链上广播，没有生产发布。

## 剩余风险

- 本轮清理服务器后，同步器按缺失保护阈值进入确认流程；如果需要立即本地最终删除状态，需要按既定收敛命令单独处理测试库记录。
- 机器人真机点击和高并发测试仍受 Telegram 网络连通性影响，需要恢复连通后继续。
