# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-05 22:19 CST
- 状态：按用户要求完成 Telegram 个人号监听通知过滤调整。
- 本轮范围：群组/频道 Bark 推送开关、机器人发送者过滤、相关 bot 聚焦测试。
- 本轮结论：群组和频道消息默认不推送；只有后台人工打开 `TelegramGroupFilter.push_enabled` 后才允许推送；发送者是 Telegram bot 的消息不会触发监听推送。

## 最近验证

- 编译：`DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/telegram_listener.py bot/services.py bot/tests.py`
- 聚焦测试：`DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test bot.tests.TelegramListenerPushTestCase bot.tests.TelegramMessageRecordingTestCase.test_group_push_switch_defaults_off_and_can_be_enabled --settings=shop.settings --verbosity=2`
- 基础检查：`DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check`
- 结果：编译通过；14 个监听推送/群组开关聚焦测试通过；`manage.py check` 无问题。

## 剩余风险

- SQLite 聚焦测试仍会输出不支持 `db_comment` / `db_table_comment` 的预期 warning。
- 本轮未执行真实 Telegram 发送、真实 Bark 推送、真实支付、链上广播、云资源创建或生产发布。

## 下一步

- 如需进一步确认，可在后台手动打开某个群/频道的通知开关，并用测试消息观察 Bark 是否只对该会话触发。
