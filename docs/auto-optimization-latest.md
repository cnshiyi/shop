# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-04 20:56 CST
- 状态：按用户要求完成 Telegram bot 真机重测；实际创建 1 台 AWS Lightsail 测试服务器并完成开通，随后真实删除实例并释放固定 IP；同时修复重装确认页旧文案。
- 本轮范围：主菜单、购买节点、新购钱包支付、云服务器开通、个人中心、订单筛选、余额筛选、充值提示、提醒列表、地址监控、代理列表、自动续费查询、IP 查询、续费入口、换 IP 入口、重装确认页、修改配置入口、联系客服。
- 本轮修复：`core/texts.py` 中 `bot_reinstall_confirm`、`bot_reinstall_validate_ok`、`bot_reinstall_need_main_link` 默认文案统一为“重建迁移”；`bot/tests.py` 增加全局文案反向断言，防止“确认重新安装/重新安装大约/期间代理可能会断连”回流。
- 本轮结论：bot 真机主流程可用；重装确认页已重测为“确认重建迁移”；测试云资源已清理。

## 最近验证

- 真机：`@ceshiayan_bot` 轮询启动成功，项目数据库内 `TelegramLoginAccount #1` 可登录并能真实发送 `/start`、点击 inline 按钮。
- 真机：新购订单 `#90` 创建 AWS Lightsail 实例成功，固定 IP 绑定成功，BBR、MTProxy 主代理、备用代理、Telemt 多端口和 SOCKS5 安装成功；随后实例真实删除、固定 IP 真实释放，本地订单/资产为 `deleted`。
- `DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile core/texts.py bot/tests.py bot/handlers.py` 通过。
- `DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase.test_legacy_custom_port_flow_is_removed bot.tests.RetainedIpRenewalUiTestCase.test_reinstall_cancel_buttons_keep_back_path bot.tests.RetainedIpRenewalUiTestCase.test_reinstall_confirm_handlers_reuse_saved_back_path_after_submit --settings=shop.settings --verbosity=2` 通过。
- `DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check` 通过。
- `git diff --check` 通过。

## 剩余风险

- 本轮执行了真实 AWS Lightsail 创建、删除和固定 IP 释放；完整脱敏记录已写入 `docs/real-machine-test-report.md`。
- 本轮没有执行链上广播或真实地址充值到账。
- TRON 扫块器在真机 bot 运行期间出现若干 429/ReadTimeout 重试日志，不影响本轮 bot 点击路径，但仍属于生产可观测风险。

## 下一步

- 如果继续真机深测，建议单独验证“续费入口点击即进入待支付续费状态”的产品预期；本轮只确认入口可用，并未保留待支付状态。
