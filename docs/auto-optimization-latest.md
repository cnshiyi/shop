# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-10 18:22 CST
- 状态：已完成第三轮授权真机并行复测、云端残留补清，以及 Telegram 备用通知链接兜底修复。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 前端分支：未改动
- 目标主分支：`main`

## 本轮背景

- 用户要求：再并行测试一轮。
- 用户反馈：云服务器安装完成通知发送失败，Telegram 报错 `inline keyboard button URL ... is invalid`。
- 用户最终要求：备用通知改为 `https://t.me/sy168`。

## 修复内容

- `bot/keyboards.py`
  - 新增默认客服链接 `https://t.me/sy168`。
  - 支持把 `@用户名`、裸 Telegram 用户名、`t.me/...` 和 `telegram.me/...` 归一化为 HTTPS 链接。
  - 对 `https://shiyi4` 这类 Telegram 不接受的无效 URL，自动兜底到默认客服链接，避免通知发送被按钮 URL 阻断。
- `bot/tests.py`
  - 新增客服按钮 URL 回归测试，覆盖无效 URL 兜底、`@用户名` 归一化、配置读取异常兜底。

## 真机压测

- 隔离数据库：`.shop-load-tests/shop-loadtest-realmachine-third.sqlite3`
- 报告文件：`.shop-load-tests/real-machine-parallel-install-report-third.json`
- 云账号：AWS Lightsail 后台账号 `#55`，区域 `ap-southeast-1`。
- 套餐：`#131`，`实机测试 Nano`，`nano_3_0`。
- 结果：
  - 第 1 轮并行提交 5 个创建安装任务，4 台完成创建和代理安装，1 台因资源/配额链路失败进入失败清理。
  - 第 2 轮并行触发重装、重建、修改配置：重建迁移完成，修改配置迁移完成；重装入口仍按现有 `completed` 状态跳过。
  - 第 3 轮再次并行触发创建、重装、修改配置：新增创建因固定 IP 配额限制失败，修改配置迁移完成，重装仍按现有逻辑跳过。
  - 本轮没有复现远端安装锁权限错误。
- 清理：
  - 脚本自动清理 `LOAD...` 测试订单资源。
  - 手动补清 3 台 `SRVREBUILD...` / `SRVUPGRADE...` 迁移订单实例。
  - AWS 只读复核：测试前缀实例列表为空，固定 IP 列表为空。

## 验证

通过：

```bash
uv run python -m py_compile bot/keyboards.py bot/tests.py
uv run python manage.py check
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase.test_support_contact_button_falls_back_when_config_url_is_invalid_for_telegram bot.tests.RetainedIpRenewalUiTestCase.test_support_contact_button_normalizes_telegram_username bot.tests.RetainedIpRenewalUiTestCase.test_support_contact_button_uses_default_when_config_load_fails --keepdb --noinput --verbosity 1
```

## 风险和下一步

- AWS Lightsail 固定 IP 配额仍会限制并行创建数量；当前失败订单会进入清理路径，符合“至少 5 台或达到配额限制”的测试条件。
- 重装入口在 `completed` 订单上由 `reprovision_cloud_server_bootstrap()` 跳过；如需原机重新安装代理，需要单独调整允许状态。
- 修改后所有“联系客服”按钮默认都会生成 URL 按钮，不再回落到 bot 内 callback；这符合本轮备用通知链接要求。
