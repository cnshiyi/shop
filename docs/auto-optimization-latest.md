# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-13 10:06 CST
- 状态：已修复旧机保留/等待删除 IP 查询仍显示续费、开机、自动续费按钮的问题，并完成隔离库并行压测。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 目标主分支：`main`

## 本轮背景

- 用户反馈机器人 IP 查询结果中，IP 状态已是 `旧机保留期，等待删除（云端运行中）`，但键盘仍显示 `续费IP`、`开机`、`修改时间`、`开启自动续费`。
- 重复点击 `cloud:renew:<order_id>:cloud:querymenu` 后，日志显示回调快速完成，但页面无明显反馈。
- 本轮只处理用户点名的机器人续费按钮回调和查询键盘问题，未执行真实云资源创建、解绑、释放、删除、真实支付或链上广播。

## 修改摘要

- `bot/handlers.py`
  - IP 查询走 `CloudAsset` 结果时，如果云端状态文本包含 `旧机保留期` 或 `等待删除`，不再把关联订单作为可操作续费/开机上下文。
  - 旧机保留/等待删除资产隐藏 `续费IP`、`开机`、`开启/关闭自动续费` 等会误导用户的动作按钮。
  - 正常活跃服务器仍保留续费、开机、换 IP、重装和配置等原有动作。
  - `cloud:renew` 回调移除开头的静默 `answerCallbackQuery`，避免后续“续费订单创建失败”等 alert 被提前确认吞掉；成功进入续费计划或支付提示后再确认回调。
- `bot/tests.py`
  - 新增 `test_admin_ip_query_old_retained_instance_hides_renew_start_actions`。
  - 覆盖已完成订单的资产即使本地 `status=running`、有实例 ID 和登录密码，只要 `provider_status` 是旧机保留/等待删除，就不再展示续费、开机和自动续费按钮。

## 并行压测结果

- 独立数据库：`.shop-load-tests/shop-loadtest-bot-renew-parallel.sqlite3`
- 报告文件：`.shop-load-tests/bot-renew-parallel-report.json`
- 规模：
  - 旧机保留/等待删除资产：500 条。
  - 正常活跃服务器资产：500 条。
  - 总查询：1000 次。
  - 并发：80。
- 结果：
  - 失败数：0。
  - QPS：约 `925.25`。
  - `p50=85.991ms`，`p95=88.807ms`，`p99=89.154ms`，`max=89.4ms`。
  - 旧机保留样本按钮只剩 `profile:back_to_menu`。
  - 正常活跃样本仍包含 `cloud:renew`、`cloud:start`、`cloud:ip`、`cloud:reinit`。

## 验证命令

通过：

```bash
uv run python -m py_compile bot/handlers.py bot/tests.py
uv run python manage.py test bot.tests.BotOrderAndBalanceFilterTestCase.test_admin_ip_query_old_retained_instance_hides_renew_start_actions bot.tests.RetainedIpRenewalUiTestCase.test_cloud_ip_query_actions_return_to_query_menu bot.tests.BotOrderAndBalanceFilterTestCase.test_admin_query_keyboard_includes_reinstall_and_expiry_actions --keepdb --noinput --verbosity 2
uv run python manage.py check
git diff --check
```

并行压测命令：

```bash
uv run python manage.py prepare_load_test_db --sqlite-name .shop-load-tests/shop-loadtest-bot-renew-parallel.sqlite3 --migrate --confirm-isolated
DB_ENGINE=sqlite SQLITE_NAME=.shop-load-tests/shop-loadtest-bot-renew-parallel.sqlite3 SHOP_LOAD_TEST_DB=1 uv run python manage.py shell < /tmp/bot_renew_parallel_pressure.py
```

## 风险和下一步

- 本轮压测库和报告保留在 `.shop-load-tests/`，不提交数据库文件；清理策略为删除本轮 `shop-loadtest-bot-renew-parallel.sqlite3` 和 `bot-renew-parallel-report.json`。
- 本轮只调整机器人查询键盘和续费回调确认时机，不改生命周期执行器、云同步、真实续费计费或支付链路。
- 线上机器人进程需要重启后才会加载本轮 `bot/handlers.py` 变更。
