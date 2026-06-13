# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-13 13:08 CST
- 状态：已修正并真机验证机器人 IP 查询状态续费入口：关机服务器可开机，旧机保留服务器可续费，未附加未删除固定 IP 可走资产续费，已删除未附加 IP 不可续费。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 目标主分支：`main`

## 本轮背景

- 用户要求在真实机器人和真实 AWS 测试资源上验证各状态续费按钮。
- 授权范围：使用后台 AWS 测试账号 `#55` / `ap-southeast-1` 创建 nano 实例和固定 IP，执行关机、解绑、删机、同步、机器人查询验证，并在结束后释放资源。
- 发现并修正补充口径：有关联订单的未附加固定 IP，如果 IP 尚未删除，也应显示资产续费入口，而不是被关联订单阻断。

## 修改摘要

- `bot/handlers.py`
  - 未附加固定 IP 的资产续费入口不再因为存在历史关联订单而被隐藏。
  - 仍保持已删除/已释放未附加 IP 不可续费。
  - 旧机保留/等待删除服务器仍走订单续费入口，且继续隐藏开机、自动续费等普通运行态操作。
- `bot/tests.py`
  - 新增覆盖：有关联订单、订单处于保留期、云端状态为未附加固定 IP 时，IP 查询显示 `cloud:aa:renew:<asset_id>`，不显示 `cloud:renew:<order_id>` 或 `cloud:start:<order_id>`。
- `docs/real-machine-test-report.md`
  - 追加 2026-06-13 Bot IP 查询状态续费真机测试记录，资源名和公网 IP 已脱敏。

## 真机测试结果

- 真实创建 AWS Lightsail nano 实例和固定 IP，并绑定到实例。
- 关机服务器：
  - Bot 查询显示 `续费IP` 和 `开机`。
  - 实际点击 `开机` 后进入 `cloud:start` 回调，AWS 实例恢复为 `running`。
- 旧机保留/等待删除服务器：
  - Bot 查询显示 `续费IP` 和 `修改时间`。
  - 不显示 `开机`、自动续费、换 IP、重装、修改配置。
  - 实际点击 `续费IP` 后进入续费支付入口，未点击钱包支付或地址支付。
- 未附加但未删除固定 IP：
  - Bot 查询显示资产续费入口 `cloud:aa:renew`。
  - 实际点击后进入恢复新服务器套餐选择页，未提交代理链接、未创建支付订单。
- 已释放/已删除未附加 IP：
  - Bot 查询返回“未查询到可续费的有效 IP 记录”，无续费按钮。
- 清理复核：
  - 测试实例不存在。
  - 测试固定 IP 不存在。
  - 临时 `bot_admin_chat_id` 已还原为空。
  - 本地测试订单和资产已标记为 `deleted`。

## 验证命令

已通过：

```bash
uv run python -m py_compile .shop-load-tests/real_bot_ip_state_test.py
uv run python .shop-load-tests/real_bot_ip_state_test.py
uv run python manage.py test bot.tests.BotOrderAndBalanceFilterTestCase.test_admin_ip_query_unattached_asset_with_order_keeps_asset_renewal bot.tests.BotOrderAndBalanceFilterTestCase.test_admin_ip_query_old_retained_instance_keeps_renew_but_hides_runtime_actions bot.tests.BotOrderAndBalanceFilterTestCase.test_admin_ip_query_unattached_deleted_asset_hides_renew_actions bot.tests.BotOrderAndBalanceFilterTestCase.test_admin_ip_query_expired_retained_order_hides_renew_actions --keepdb --noinput --verbosity 2
uv run python -m py_compile bot/handlers.py bot/tests.py
uv run python manage.py check
git diff --check
```

真机清理只读复核通过：

```bash
AWS Lightsail prefix=codex-botstate-20260613130430: instances=[], static_ips=[]
SiteConfig bot_admin_chat_id=''
```

## 风险和下一步

- 本轮执行了真实 AWS 创建、关机、开机、解绑固定 IP、释放固定 IP 和删除实例；操作前已获用户明确授权。
- 本轮没有真实支付、链上广播、钱包扣款或地址支付。
- `CloudAsset.actual_expires_at` 仍是资产到期事实；订单 `ip_recycle_at` 只用于关联订单是否仍处在保留期的按钮准入判断。
- 线上机器人进程需要重启后才会加载本轮 `bot/handlers.py` 变更。
