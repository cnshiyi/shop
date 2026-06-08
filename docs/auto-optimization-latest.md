# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 22:51 CST
- 状态：完成 TRX 汇率强制刷新容错修复；当外部汇率接口失败时，允许回退到 Redis 最近一次成功汇率。
- 后端提交：代码修复已提交为 `4a5a9d3`；本轮文档记录随当前提交提交。
- 前端提交：无前端代码变更。

## 本轮背景

- `TODO.md` 中显式待办均已完成，本轮从当前工作树已有的最小安全改动继续收敛。
- 开始时工作树已有 `orders/services.py` 和 `orders/tests.py` 未提交改动。
- 改动范围只涉及订单域汇率读取和聚焦测试，不执行真实支付、链上广播、真实云资源操作、生产发布或删除数据。

## 修复内容

- `orders/services.py`
  - `get_trx_price(force_refresh=True)` 仍会优先尝试实时请求 Binance TRX/USDT 汇率。
  - 强制刷新期间预读 Redis 日缓存和最近成功缓存，但不直接返回，避免跳过刷新动作。
  - 如果外部接口失败，则优先回退到 Redis 最近可用汇率，再回退进程内缓存。
  - 无 Redis 缓存且无进程缓存时仍抛出原有业务错误。
- `orders/tests.py`
  - 增加强制刷新时网络失败回退 Redis 最近成功汇率的回归测试。

## 验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test orders.tests.OrderBalancePaymentTestCase.test_trx_price_uses_redis_cache_before_network orders.tests.OrderBalancePaymentTestCase.test_trx_price_uses_last_known_redis_cache_before_network orders.tests.OrderBalancePaymentTestCase.test_trx_price_force_refresh_falls_back_to_last_known_redis_cache --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile orders/services.py orders/tests.py
git diff --check
```

补充巡检：

```bash
rg -n "service_expires_at|order.*actual_expires_at|actual_expires_at.*order|plan snapshot|计划快照表|refund|退款|accounts|finance|mall|monitoring|dashboard_api|biz" --glob '!docs/refactor-version-record.md' --glob '!docs/auto-optimization-latest.md' --glob '!docs/real-machine-test-report.md' --glob '!*.pyc'
rg -n "actual_expires_at" cloud orders bot core --glob '!*/migrations/*'
```

说明：

- SQLite 聚焦测试仍输出既有 `db_comment/db_table_comment` 能力差异告警，不属于本轮问题。
- 红线扫描命中主要为既有文档、迁移历史、兼容 helper 命名和当前 `CloudAsset.actual_expires_at` 口径使用；本轮未恢复废弃 runtime app、订单到期字段、旧计划快照或旧退款入口。

## 剩余风险

- `bot.runner` 强制刷新汇率依赖外部 Binance 接口；本轮只保证接口失败时使用最近缓存，不改变汇率来源和刷新频率。
- 未做真实链上支付或生产环境长时间运行验证。
