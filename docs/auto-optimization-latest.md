# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 14:31 CST
- 状态：完成一轮代理列表 `account_disabled` 慢路径优化、真实前端翻页对账和机器人多任务并发测试。
- 本轮范围：
  - `cloud/api_asset_snapshots.py`：代理列表风险计数热路径。
  - `cloud/models.py` / `cloud/migrations/0063_account_disabled_snapshot_page_index.py`：云账号异常标签默认列表分页索引。
  - `cloud/tests.py`：风险统计防回退测试。

## 修复结论

- 真实 MySQL 采样确认，上一轮把风险统计合成单条 `aggregate()` 后，冷缓存耗时约 `4.865s`，慢于按索引拆分 `count()` 的约 `0.880s`。
- 已把 `_dashboard_snapshot_risk_counts()` 改回按风险标签拆分 `count()`，继续复用已有缓存键和缓存版本。
- `account_disabled` 默认分页 EXPLAIN 原先命中 `Using filesort`，第 1 页冷读曾到 `8.456s`。
- MySQL 已到单表 `64` 个索引上限，不能继续堆索引；本轮删除旧窄索引 `cad_risk_display_idx`，替换为 `cad_acct_list_page_idx`：
  - `risk_account_disabled`
  - `risk_rank`
  - `asset_due_sort_null_rank`
  - `asset_due_sort_at`
  - `-sort_order`
  - `-asset_id`
- 迁移 `cloud.0063_account_disabled_snapshot_page_index` 已应用到本地真实库，`migrate --plan` 无待执行项。

## 真实库验证

优化后真实 MySQL 采样：

- `risk_counts_split_cold`：约 `1.564s`。
- `account_disabled` 第 1 页：约 `0.125s`，`loaded=20`，`total=1145002`。
- `account_disabled` 第 2 页：约 `0.111s`，`loaded=20`。
- `account_disabled` 第 1000 页：约 `0.732s`，`loaded=20`。
- `account_disabled` 最后一页 `57251`：约 `0.090s`，`loaded=2`。
- EXPLAIN 已使用 `cad_acct_list_page_idx`，不再出现 `Using filesort`。

## 真实前端复测

使用系统 Chrome 打开 `http://127.0.0.1:5666/admin/cloud-assets`，点击“云账号异常”并实际跳页：

- 第 1 页：接口 `loaded=20`，DOM 数据行 `20`，`total=1145002`，首末行 IP 与接口一致。
- 第 1000 页：接口 `loaded=20`，DOM 数据行 `20`，首末行 IP 与接口一致。
- 最后一页 `57251`：接口 `loaded=2`，DOM 数据行 `2`，首末行 IP 与接口一致。
- 页面侧接口耗时约 `801ms`、`961ms`、`688ms`。
- 控制台 `0` error / `0` warning，请求 `0` 个 400/500。
- 临时后台用户和临时 session 已清理。

## 机器人测试

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests --settings=shop.settings --verbosity=1
```

结果：

- `107` 条机器人测试通过。
- 覆盖批量钱包直付/补付并发任务、消息转发隔离、云资产/订单返回链、续费、换 IP、重装、修改配置、callback 长度压缩等回归。

## 后端验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py migrate --plan
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_all_includes_disabled_or_missing_cloud_account_assets cloud.tests.CloudServerServicesTestCase.test_cloud_assets_list_risk_counts_keep_disabled_account_isolated cloud.tests.CloudServerServicesTestCase.test_cloud_asset_dashboard_risk_counts_do_not_use_single_aggregate --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests --settings=shop.settings --verbosity=1
git diff --check
```

红线扫描通过：

```bash
rg -n "service_expires_at|actual_expires_at.*CloudServerOrder|CloudServerOrder.*actual_expires_at|plan snapshot|snapshot table|old refund|refund_legacy|refund_old|legacy_refund|accounts\\.|finance\\.|mall\\.|monitoring\\.|dashboard_api\\.|biz\\." cloud bot orders core shop -g '!**/migrations/**'
```

命中项仍为 Telegram 登录账号代码、云账号测试桩和 `CloudServerOrder.ip_recycle_at` 同步语句，不是旧到期事实回流。

## 受限项

- 本轮未执行真实云资源创建、关机、删机、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未打印密钥、Telegram session、支付密钥、云厂商密钥或完整代理链接到仓库文件。
- 本轮使用的是本地真实 MySQL 大表和真实前端页面，不是只看 API 计数。

## 下一步

- 继续按固定巡检清单跑下一轮，重点看生命周期计划页、通知计划页和代理列表其他高基数标签是否还有类似 `filesort` 或索引上限问题。
- 后续新增索引前必须先检查 MySQL 单表 64 索引上限，优先替换低价值旧索引，不继续堆补丁。
