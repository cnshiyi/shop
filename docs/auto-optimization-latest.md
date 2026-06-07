# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 07:03 CST
- 状态：完成一轮 Telegram 机器人全功能回归和多任务高并发巡检；未发现需要修改代码的问题。
- 本轮范围：机器人 callback 返回链、64 字节限制、资产详情、订单详情、续费、钱包支付续费、换 IP、重装/重建迁移、修改配置、通知复制并发、云服务器后台钱包任务并发。

## 巡检结论

- `bot.tests` 整组 `106` 个测试全部通过。
- 覆盖重点：
  - 云服务器详情按钮保留返回路径。
  - 资产详情入口直接操作按钮会压缩返回来源。
  - 极端嵌套 callback 仍不超过 Telegram `callback_data` 64 字节限制。
  - 续费支付按钮、换 IP 区域菜单、重装确认/提交按钮保留返回链。
  - 普通重装不回流为旧逻辑，确认处理仍走重建/迁移语义。
  - 订单列表、订单只读详情和资产详情的返回按钮在来源过长时回退到安全入口。
  - 通知复制 wrapper 并发发送隔离。
  - 云服务器后台钱包直付/补付任务并发隔离，用户、订单和任务数量没有串上下文。
- 测试日志中的代理 secret 已按现有日志策略脱敏，没有输出完整代理链接。

## 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
git diff --check
```

红线扫描通过：

```bash
rg -n "service_expires_at|actual_expires_at.*CloudServerOrder|CloudServerOrder.*actual_expires_at|plan snapshot|snapshot table|old refund|refund_legacy|refund_old|legacy_refund|accounts\\.|finance\\.|mall\\.|monitoring\\.|dashboard_api\\.|biz\\." cloud bot orders core shop -g '!**/migrations/**'
```

说明：

- SQLite 的 `db_comment` warnings 仍是已知测试噪声。
- 本轮没有创建临时后台 session。
- 未保留 Playwright 临时产物。

## 受限项

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮没有打印密钥、Telegram session、支付密钥或完整代理链接。
- 本轮没有业务代码改动，仅记录巡检结果。

## 下一步

- 继续不停轮巡检，下一轮优先做代理列表深页/跳页数据对账，尤其是 IP 视图各风险标签第 2 页、深页、末页是否与数据库精确结果一致。
- 继续关注云账号异常标签冷缓存加载时间。
