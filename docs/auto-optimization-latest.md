# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 20:54 CST
- 状态：完成机器人高并发真机测试前置巡检；Telegram 网络不可达导致真机点击未能继续，但本轮发现并修复 bot 启动会同时触发后台生命周期/支付扫块/通知扫描的隔离缺口。
- 后端 Commit：已提交，`fix: isolate bot interaction patrol`。
- 前端 Commit：本轮无前端变更。

## 本轮覆盖范围

- 后端仓库：`/Users/a399/Desktop/data/shop`
- 机器人入口：`bot/runner.py`
- 机器人测试：`bot/tests.py`
- 临时日志：`/private/tmp/shop-bot-patrol.log`、`/private/tmp/shop-bot-interaction-only.log`

## 机器人现状

- 后端 web 服务仍在 `127.0.0.1:8000` 运行。
- 本轮开始时没有发现正在运行的 `run.py bot` / `bot.runner` 进程。
- 数据库里有 `1` 个 Telegram 登录账号：
  - `has_session=True`
  - 状态检查后仍为 `listener_error`
  - 连接 Telegram MTProto 连续超时，不能作为真机点击账号使用。
- bot token 已配置，但单独 `getMe` 连通性检查返回 `TelegramNetworkError`。
- 因 Telegram 网络不可达，本轮没有完成真实 Telegram 点击或高并发真实消息压测。

## 暴露问题

首次启动 `run.py bot` 时，虽然 Bot API 获取身份超时，但进程继续启动了：

- TRON 顺序扫块器
- 资源巡检
- 云服务器生命周期调度
- 删机计划表刷新
- 通知计划表刷新
- 自动续费巡检
- 云账号状态巡检
- Telegram 个人号消息监听
- 启动时云服务器生命周期检查

这会污染“只测试机器人交互”的巡检结果，并可能触发真实生命周期扫描和通知发送，不适合高并发真机点击测试。

## 修复内容

- `bot/runner.py`
  - 新增 `SHOP_BOT_BACKGROUND_TASKS_ENABLED` 环境开关。
  - 默认值为开启，生产行为不变。
  - 设置 `SHOP_BOT_BACKGROUND_TASKS_ENABLED=0` 时，只启动 Telegram Bot 交互路径，不启动 TRON 扫块、生命周期调度、计划刷新、自动续费、云账号巡检、个人号监听和启动时生命周期检查。
  - 关闭流程改为只取消实际创建过的后台任务，避免禁用模式关闭时报空任务。
- `bot/tests.py`
  - 新增 `BotRunnerConfigTestCase`，固定开关默认开启、显式关闭和显式开启的解析行为。

## 实测结果

普通 bot 启动：

- 机器人进程能启动。
- Redis/FSM 初始化成功。
- `get_me` 超时后进入默认 Bot 标签。
- 因没有隔离开关，后台调度和启动时生命周期检查会被启动。
- 已手动停止该进程，当前没有残留 `run.py bot` / `bot.runner` 进程。

交互专用模式：

```bash
SHOP_BOT_BACKGROUND_TASKS_ENABLED=0 SHOP_BOT_KEEPALIVE=0 uv run python run.py bot
```

- 机器人进程能启动。
- Redis/FSM 初始化成功。
- `get_me` 仍因 Telegram 网络返回超时。
- 日志明确显示：`机器人后台任务已禁用：SHOP_BOT_BACKGROUND_TASKS_ENABLED=0，仅启动 Telegram Bot 交互`。
- 没有再启动 TRON 扫块、生命周期调度、通知计划刷新、自动续费巡检、个人号监听或启动时生命周期检查。
- 已手动停止该进程，当前没有残留 `run.py bot` / `bot.runner` 进程。

## 本轮验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/runner.py bot/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.BotRunnerConfigTestCase --settings=shop.settings --verbosity=1
git diff --check
```

红线扫描通过：

```bash
rg -n "service_expires_at|actual_expires_at.*CloudServerOrder|CloudServerOrder.*actual_expires_at|plan snapshot|snapshot table|old refund|refund_legacy|refund_old|legacy_refund|accounts\.|finance\.|mall\.|monitoring\.|dashboard_api\.|biz\." cloud bot orders core shop -g '!**/migrations/**'
```

命中项为既有允许项：bot 测试桩、Telegram 登录账号模块名、`CloudServerOrder.ip_recycle_at` 同步记录，不是旧订单到期事实、旧计划快照或废弃 runtime app 回流。

## 结论

- 机器人真机点击和高并发真实消息压测当前被 Telegram 网络 `TelegramNetworkError` / MTProto `TimeoutError` 阻断。
- 本轮已经把“机器人交互巡检”和“后台生命周期/支付/通知任务”隔离开，后续网络恢复后可以用 `SHOP_BOT_BACKGROUND_TASKS_ENABLED=0` 安全启动 bot 做真机点击，不会误触发后台生命周期扫描。
- 本轮没有执行真实支付、链上广播、真实云资源创建/删除或生产发布。
- `docs/real-machine-test-report.md` 当前存在既有未提交真实机器测试记录，本轮不覆盖、不提交。

## 已完成压测

- 生命周期计划页：关机计划、删除计划、服务器删除历史、IP 删除计划、IP 删除历史的深分页和真实前端翻页。
- 代理列表：全部 11 个标签均完成 10 万级以上压测，覆盖第 1 页、第 2 页、第 1000 页和最后页，并完成真实前端点击。
- 任务中心：250 万级统计汇总和真实前端展示已完成。
- 通知计划页：21429 活跃分组和 14960 历史通知的深分页、跳页、列开关和真实前端展示已完成。

## 尚未完成

- 机器人多任务高并发真机点击压测还没有完成，当前阻塞点是 Telegram 网络不可达和登录账号状态 `listener_error`。
- 真实云资源创建、到期关机、删机、释放 IP 的生命周期开关矩阵还没有完整闭环。
- 服务器创建后的完整生命周期链路还没有在本轮压测中闭环到真实关机、真实删机和真实 IP 释放。
