# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-07 19:32 CST
- 状态：修复代理列表 150 万资产下快照缺失导致的孤儿资产不可见问题，并完成真实浏览器页面验证。
- 本轮范围：代理列表快照投影补齐、百万级快照补齐命令安全上限、真实前端首页与最后页跳页、数据库数量和风险统计对账。

## 修复摘要

- 发现真实数据库有 `CloudAsset` 1500000 条，但 `CloudAssetDashboardSnapshot` 只有 500000 条，导致新增的 1000000 条资产不进入代理列表页面。
- 新增快照缺失分批补齐逻辑：少量缺失在请求内补齐，大量缺失只启动带锁后台补齐，避免页面请求同步跑百万级刷新。
- `refresh_cloud_asset_dashboard_snapshots` 管理命令改为默认只补齐缺失快照，批次上限固定为 10000，避免 50000 批次触发 MySQL `max_allowed_packet`。
- 旧快照刷新改为显式 `--include-stale`，默认不再进入百万级旧快照扫描，避免维护命令和列表请求因全表扫描超时。
- 缺失检测先比较资产表与快照表数量，已对齐时不再执行反关联缺失查询。

## 数据与实测

- 修复前：资产 1500000，快照 500000，缺失 1000000，页面只显示 `全部 (500000)`。
- 已执行真实库补齐：资产 1500000，快照 1500000，缺失 0。
- 修复后页面显示：`全部 (1500000)`、`云账号异常 (1045002)`、可见分组 `1489996`。
- 数据库对账：可见快照 1489998，云账号异常 1045002，运行中非云账号异常 449988，即将到期 1250，已过期 1752，未附加固定 IP 1。
- 真实浏览器第 1 页显示 `huangyating6748`、`压测Y用户00000`、`198.19.0.0`、`5.12 USDT`。
- 真实浏览器跳到最后页第 74500 页，显示 `压测用户Z98729` 到 `压测用户Z99719`，不是第 1 页重复数据。
- 浏览器控制台：0 error / 0 warning。

## 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_asset_dashboard_snapshot_backfill_materializes_missing_assets cloud.tests.CloudServerServicesTestCase.test_cloud_asset_dashboard_snapshot_backfill_skips_stale_by_default cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_defers_large_missing_snapshot_backfill cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_defers_large_stale_snapshot_refresh cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_all_includes_disabled_or_missing_cloud_account_assets --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/api_asset_snapshots.py cloud/management/commands/refresh_cloud_asset_dashboard_snapshots.py cloud/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py refresh_cloud_asset_dashboard_snapshots --batch-size 50000 --max-batches 2
git diff --check
git -C /Users/a399/Desktop/data/vue-shop-admin diff --check
```

结果：5 个快照补齐/大数据列表聚焦测试、Django 系统检查、编译检查、管理命令安全返回和前后端空白检查均通过。SQLite 的 `db_comment` 警告仍为已知数据库能力差异。

## 红线

- 本轮未执行真实云资源创建、删除、关机、释放 IP、换 IP、真实支付、链上广播、删除业务数据、删除测试库或生产发布。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥或云厂商密钥。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款入口或旧兼容壳。

## 剩余风险

- 本轮修复的是代理列表快照投影完整性和页面可见性；任务中心、生命周期计划、通知计划仍需继续做真实页面跳页和数据库对账。
- 当前没有 `logged_in` 状态的 Telegram 登录账号，机器人真机账号点击测试仍无法完成，只能继续跑回调与菜单聚焦测试。
