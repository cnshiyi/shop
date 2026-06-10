# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-10 17:36 CST
- 状态：已完成 SOCKS5 链路格式调整、并行安装互斥修复、replacement 迁移资产唯一约束修复，并完成授权真机并行压测与资源清理。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 前端分支：未改动
- 目标主分支：`main`

## 本轮背景

- 用户要求：安装任务不能被其他任务打断，应支持多个服务器同时安装。
- 用户要求：SOCKS5 输出改为 `https://t.me/socks?server=...&port=9534&user=...&pass=...`。
- 用户已授权真实云资源成本，压测范围为安装、重装、修改配置交替并行触发，至少 5 台或达到配额限制，3 轮以上，并在测试后删除服务器。

## 修复内容

- `cloud/bootstrap.py`
  - SOCKS5 输出改为 `https://t.me/socks?...&user=...&pass=...`。
  - 远端同机安装锁改为先 root/sudo 预创建并 `chmod 0666`，再追加打开并 `flock`，避免 BBR 和 MTProxy 阶段权限不一致。
  - SSH 远端脚本超时按阶段放宽，避免长时间安装被过早中断。
- `cloud/provisioning.py` / `cloud/services.py` / `bot/handlers.py`
  - 解析、保存、展示兼容 `socks5://`、`tg://socks?` 和 `https://t.me/socks?`。
  - replacement 订单保存资产前释放源资产固定 IP 占用，避免 `CloudAsset.public_ip` 唯一约束冲突。
- `cloud/api_orders.py` / `cloud/api_assets.py` / `bot/api.py`
  - 代理链接脱敏和备注压缩支持新的 SOCKS 链接格式。
- `cloud/tests.py` / `bot/tests.py`
  - 更新 SOCKS5 新格式断言。
  - 新增 replacement 迁移资产唯一约束回归测试。

## 真机压测

- 隔离数据库：
  - `.shop-load-tests/shop-loadtest-realmachine.sqlite3`
  - `.shop-load-tests/shop-loadtest-realmachine-rerun.sqlite3`
- 云账号：AWS Lightsail 后台账号 `#55`，区域 `ap-southeast-1`。
- 套餐：`#131`，`实机测试 Nano`，`nano_3_0`。
- 首轮 5 并发创建真实触发后发现远端锁权限问题；已修复并清理，AWS 残留复核为空。
- 修复后复测：
  - 5 个创建任务并行提交。
  - 3 台创建安装成功，2 台达到固定 IP 配额限制。
  - SOCKS5 链路输出为 `https://t.me/socks?server=...&port=9534&user=***&pass=***`。
  - 重建迁移和修改配置迁移均完成固定 IP 迁移及代理安装。
  - 重装入口按当前服务函数对 `completed` 状态跳过，已记录为现有行为。
- 清理：
  - 脚本自动清理 `LOAD...` 测试订单资源。
  - 手动补清 `SRVREBUILD...` / `SRVUPGRADE...` 迁移订单资源。
  - AWS 只读复核：测试前缀实例列表为空，固定 IP 列表为空。

## 验证

通过：

```bash
uv run python -m py_compile cloud/bootstrap.py cloud/provisioning.py cloud/services.py cloud/api_orders.py cloud/api_assets.py bot/handlers.py bot/api.py
uv run python manage.py check
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_mtproxy_script_runs_mtg_with_fake_tls_secret cloud.tests.CloudServerServicesTestCase.test_extract_proxy_links_labels_custom_low_port_plan cloud.tests.CloudServerServicesTestCase.test_compact_proxy_install_note_removes_raw_links cloud.tests.CloudServerServicesTestCase.test_cloud_asset_note_appends_clean_install_summary cloud.tests.CloudServerServicesTestCase.test_mark_success_replacement_releases_source_asset_public_ip_before_asset_upsert --keepdb --noinput --verbosity 1
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase.test_cloud_server_created_text_includes_socks5_proxy_link bot.tests.RetainedIpRenewalUiTestCase.test_cloud_server_created_text_recovers_socks5_from_install_note bot.tests.RetainedIpRenewalUiTestCase.test_cloud_server_created_text_does_not_use_socks5_as_one_click bot.tests.RetainedIpRenewalUiTestCase.test_proxy_links_text_converts_socks5_to_telegram_link --keepdb --noinput --verbosity 1
```

## 风险和下一步

- 真机压测确认并发创建会达到 AWS 固定 IP 配额限制；当前行为是失败订单进入清理流程，符合本轮“5 台或达到配额限制”的测试条件。
- 重装入口在 `completed` 订单上由 `reprovision_cloud_server_bootstrap()` 跳过；如需“原机重新安装代理”作为用户可用功能，需要单独调整该服务函数的允许状态。
- 本轮 replacement 迁移资产唯一约束问题已通过单元测试验证，未再次消耗真实云资源做第三次复测。
