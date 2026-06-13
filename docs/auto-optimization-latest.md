# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-13 12:30 CST
- 状态：已修正机器人 IP 查询续费/开机按钮口径：旧机保留保留期内可续费，关机服务器可开机，已删除未附加 IP 不再显示续费。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 目标主分支：`main`

## 本轮背景

- 用户纠正上一轮结论：
  - 未附加 IP 只有“还未删除”时才允许续费。
  - 旧机保留/等待删除的服务器状态也可以续费。
  - 关机服务器需要显示开机入口。
- 本轮只调整机器人 IP 查询键盘和续费/开机按钮资格判断，未执行真实云资源创建、解绑、释放、删除、真实支付或链上广播。

## 修改摘要

- `bot/handlers.py`
  - 将资产查询分支的“可续费”和“可运维操作”拆开判断。
  - 旧机保留/等待删除服务器如果有关联订单，且订单仍在有效续费状态或 `deleted + ip_recycle_at` 未过期，则继续显示 `续费IP`。
  - 旧机保留/等待删除服务器不再作为普通运行态服务器显示 `开机`、换 IP、重装、配置和自动续费。
  - 未附加 IP 只有无关联订单、仍可见且未被查询层判定删除时，才走资产续费入口。
  - 普通订单查询分支显式传递 `can_start=True`，保留管理员对关机服务器的 `开机` 按钮。
  - 翻页回调同步传递 `can_start`，避免第一页和翻页后的按钮不一致。
- `bot/keyboards.py`
  - `cloud_ip_query_result()` 的订单按钮分支新增 `can_start` 控制。
  - 兼容旧调用：未传 `can_start` 时仍按原行为显示管理员开机入口。
- `bot/tests.py`
  - 覆盖旧机保留/等待删除保留期内：显示 `续费IP`，不显示 `开机` 和自动续费。
  - 覆盖旧机保留已过回收时间：不显示续费和开机。
  - 覆盖未附加 IP 已到期删除：查询为空，不显示续费/开机。
  - 覆盖普通管理员查询：仍显示开机、重装、修改配置、修改时间和自动续费。
  - 覆盖 `can_start=False` 时键盘只保留续费和修改时间，不显示开机。

## 验证命令

已通过：

```bash
uv run python -m py_compile bot/handlers.py bot/keyboards.py bot/tests.py
uv run python manage.py test bot.tests.BotOrderAndBalanceFilterTestCase.test_admin_ip_query_old_retained_instance_keeps_renew_but_hides_runtime_actions bot.tests.BotOrderAndBalanceFilterTestCase.test_admin_ip_query_unattached_deleted_asset_hides_renew_actions bot.tests.BotOrderAndBalanceFilterTestCase.test_admin_ip_query_expired_retained_order_hides_renew_actions bot.tests.BotOrderAndBalanceFilterTestCase.test_admin_query_keyboard_includes_reinstall_and_expiry_actions bot.tests.BotOrderAndBalanceFilterTestCase.test_admin_query_keyboard_can_hide_start_for_retained_server bot.tests.RetainedIpRenewalUiTestCase.test_cloud_ip_query_actions_return_to_query_menu --keepdb --noinput --verbosity 2
```

待最终收口继续运行：

```bash
uv run python manage.py check
git diff --check
```

## 风险和下一步

- 本轮只修正机器人查询键盘展示资格，不改续费支付、云资源执行器、云同步和生命周期执行逻辑。
- `CloudAsset.actual_expires_at` 仍是资产到期事实；订单 `ip_recycle_at` 只用于关联订单是否仍处在保留期的按钮准入判断。
- 线上机器人进程需要重启后才会加载本轮 `bot/handlers.py` 和 `bot/keyboards.py` 变更。
