# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 03:13 CST
- 状态：完成一轮任务中心聚合层只读审计；本轮未改业务代码。
- 本轮范围：任务中心聚合 API/测试、固定巡检清单、红线关键字扫描、Django check。

## 本轮专项

原计划继续覆盖任务中心真实前端巡检，但当前沙箱禁止访问本机回环网络：

- 浏览器/Node 访问 `127.0.0.1:5666` 返回 `EPERM`
- Django 默认 MySQL 连接 `127.0.0.1` 也返回 `Operation not permitted`

因此本轮退回为可验证的只读专项审计，重点检查任务中心聚合逻辑在测试层和静态巡检下是否存在明显回归。

## 审计结果

- `cloud.tests_task_center` 共 `14` 个聚焦测试全部通过。
- 任务中心聚合仍覆盖 `cloud_sync`、`cloud_orders`、`lifecycle`、`notices`、`auto_renew` 五个 section。
- 静态扫描未发现 runtime 代码恢复订单侧到期事实字段、旧计划快照入口、旧退款入口或废弃 runtime app 回流。
- `CloudAsset.actual_expires_at` 仍是当前 runtime 代码中的资产到期事实来源；涉及 `service_expires_at` 的命中仅见于历史迁移或日志字段语义，不是当前 runtime 回流。

## 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=1
```

已确认的环境阻断：

```bash
node -e "require('http').get('http://127.0.0.1:5666/admin/tasks').on('error', console.log)"
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py shell -c "from cloud.task_center import task_center_payload; print(task_center_payload())"
```

结果：

- 本地 HTTP 访问被沙箱拦截，报 `connect EPERM 127.0.0.1:5666`
- 默认 MySQL 连接被沙箱拦截，报 `Can't connect to MySQL server on '127.0.0.1' ([Errno 1] Operation not permitted)`
- SQLite 仍有 `db_comment` 能力差异告警，属已知差异，不影响本轮聚焦测试结果

## 红线

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款逻辑、旧退款函数名或旧兼容入口。
- 本轮未打印 Telegram token、Telegram session、TOTP、支付密钥、云厂商密钥、完整代理链接、代理 secret 或登录密码。

## 下一步

- 若后续运行环境允许访问本机回环网络和本地 MySQL，优先恢复任务中心真实浏览器巡检，对 `/admin/tasks` 做卡片点击、详情跳转、控制台和请求状态复查。
- 继续覆盖任务中心与计划页、通知计划、自动续费页之间的状态口径一致性，重点看失败/告警计数和最近失败样本是否重复或漏报。
