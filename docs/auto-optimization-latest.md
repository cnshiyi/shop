# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-10 14:35 CST
- 状态：已完成通知计划月度合并、真实 Telegram 通知验证、自动续费开关并发压测。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 前端分支：`codex/cloud-asset-list-performance`
- 目标主分支：`main`

## 本轮背景

- 用户要求确认通知计划是否按用户整合，目标是同一用户一个月只发送一次通知。
- 用户要求必须实际打开通知计划页面截图验证，不能只口头判断。
- 用户授权可把通知人设置为已登录 Telegram 账号，实际验证真实通知是否送达。
- 用户要求再压测一轮代理列表自动续费开关并发打开/关闭。

## 修复内容

- `cloud/lifecycle.py`
  - 新增 `monthly_notice` 月度合并通知事件。
  - 生命周期巡检不再按到期提醒、自动续费预提醒、删机提醒、IP 回收提醒分别给同一用户多次发送。
  - 同一用户同一自然月内命中的多类通知先按通知目标聚合，再生成一条“月度 IP 通知汇总”。
  - 当前自然月已存在成功通知日志时，不再重复发送，并把本轮命中的对应订单通知字段标记为已通知。
- `cloud/api_tasks.py`
  - 通知计划页按用户和月份合并显示。
  - 月度合并行支持展开多类通知来源，删除月度通知历史时会按 `field_order_ids` 恢复对应通知字段。
- `cloud/tests.py`
  - 增加生命周期月度合并发送测试。
  - 增加通知计划页同用户同月合并展示测试。
- `apps/web-antd/src/views/dashboard/tasks/notices.vue`
  - 通知计划页描述改为“按用户和月份整合通知计划；同一用户每月只生成一条合并文案”。

## 验证

后端通过：

```bash
uv run python -m py_compile cloud/lifecycle.py cloud/api_tasks.py cloud/tests.py
uv run python manage.py check
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_monthly_notice_merges_types_for_same_user cloud.tests.CloudServerServicesTestCase.test_notice_plan_summary_merges_same_user_month_into_one_row cloud.tests.CloudServerServicesTestCase.test_notice_plan_summary_sorts_by_next_notice_time_before_user --settings=shop.settings --verbosity=1
```

前端通过：

```bash
pnpm -F @vben/web-antd run typecheck
```

真实页面验证使用独立 SQLite 测试库 `.stress/notice_monthly.sqlite3`、前端 `127.0.0.1:5666`：

- 通知计划页面显示 1 行月度合并通知。
- 用户：`月度测试用户 @monthly_user`。
- 通知类型：`月度合并通知：到期提醒、删机提醒`。
- 计划范围：近期计划。
- IP 数量：2。
- 控制台无 error / warning。

截图和结果文件：

- `output/playwright/notice-monthly-plan.png`
- `output/playwright/notice-monthly-plan-ip-list.png`
- `output/playwright/notice-monthly-plan-result.json`
- `output/playwright/notice-monthly-plan-ip-list-result.json`

真实 Telegram 通知验证：

- 默认库使用已登录且允许通知的个人号发送。
- 通知已真实送达，`CloudUserNoticeLog` 最新 `monthly_notice` 记录为 `delivered=True`。
- 发送尝试渠道为登录账号发送成功；未打印 Telegram session、密钥或验证码。
- 临时测试订单和资产已按测试前缀删除，发送日志保留用于审计。

自动续费开关并发压测：

- 独立 SQLite 测试库 `.stress/notice_monthly.sqlite3` 中创建 12 条压测资产和 12 条对应订单。
- 浏览器页面一次性点击前 8 个自动续费开关：
  - 并发打开：8 个接口响应均为 200，页面显示 8 个“已开启”。
  - 并发关闭：8 个接口响应均为 200，页面显示 0 个“已开启”。
  - 数据库复核：12 个订单中 `auto_renew_enabled=True` 数量为 0，和关闭后页面一致。
  - 控制台无 error / warning。

截图和结果文件：

- `output/playwright/auto-renew-concurrent-dom-clicks.png`
- `output/playwright/auto-renew-concurrent-close.png`
- `output/playwright/auto-renew-concurrent-dom-clicks-result.json`
- `output/playwright/auto-renew-concurrent-close-result.json`

## 结论

- 通知计划已经从“按用户和通知类型拆行”重构为“按用户和月份合并”。
- 生命周期真实发送链路已经按月度合并发送，目标用户同月不会重复收到多类云资产通知。
- 代理列表自动续费开关并发打开/关闭在本轮 12 条真实页面样本中表现正常，没有发现接口失败、页面状态错乱或数据库状态丢失。

## 风险和下一步

- 本轮没有创建或删除真实云服务器，没有真实支付或链上广播。
- 后续可继续扩大自动续费开关并发样本，但必须继续使用独立测试库，不能污染默认业务库。
