# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-06 11:39 CST
- 状态：已完成计划页和生命周期计划分段修复；未执行真实云资源、真实支付、链上广播、删除数据或生产发布。
- 本轮范围：将后台“删除计划”改为“计划”，补齐关机计划、服务器删除计划、IP 删除计划的独立单项开关，修复 IP 删除历史记录在活动计划很多时被截断导致显示 0 的问题。

## 修改内容

- 后端为 `CloudAsset` 增加 `server_delete_enabled`、`ip_delete_enabled`，保留 `shutdown_enabled` 作为关机计划开关。
- 生命周期判断拆分为三类开关：
  - 关机计划：`shutdown_enabled`
  - 服务器删除计划：`server_delete_enabled`
  - IP 删除计划：`ip_delete_enabled`
- 计划接口新增明确字段：
  - `shutdown_plan_items`
  - `server_delete_items`
  - `shutdown_plan_count`
  - `server_delete_count`
- 保留旧 `shutdown_items` 作为服务器删除计划兼容别名，避免任务中心旧入口断裂。
- 删除计划执行顺序调整为：关机计划完成后进入服务器删除计划，服务器删除完成后再进入 IP 删除计划。
- IP 删除历史记录改为活动计划最多 `limit` 条加历史记录最多 `limit` 条，避免活动 IP 太多时历史记录被挤掉。
- 前端计划页改名为“计划”，页面顺序调整为：关机计划、删除计划、IP 删除计划、IP 删除历史记录、服务器删除历史记录。
- 前端单项开关分别写入对应字段，不再把 IP 删除开关或服务器删除开关混用为关机开关。

## 验证结论

- 后端编译通过。
- `DB_ENGINE=mysql uv run python manage.py check` 通过。
- `DB_ENGINE=mysql uv run python manage.py migrate --plan` 通过，输出无待执行迁移。
- 聚焦测试通过，覆盖：
  - 关机计划未完成时不进入服务器删除计划；
  - 关机、服务器删除、IP 删除三个资产单项开关分别生效；
  - compact/limit 场景下 IP 删除历史记录仍返回，不再显示 0。
- 前端 `@vben/web-antd` 类型检查通过。

## 最近验证

```bash
DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/api.py cloud/lifecycle.py cloud/lifecycle_execution.py cloud/api_asset_edit.py cloud/models.py cloud/tests.py
DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py migrate --plan
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_split_shutdown_before_server_delete cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_use_stage_specific_asset_switches cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_compact_request_keeps_ip_delete_history_item --settings=shop.settings --verbosity=2
pnpm -C /Users/a399/Desktop/data/vue-shop-admin --filter @vben/web-antd typecheck
```

## 红线

- 本轮未执行真实云资源创建、删除、关机、释放 IP、换 IP、真实支付、链上广播、删除数据或生产发布。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥或云厂商密钥。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照或旧退款入口。

## 剩余风险

- 本地 50 万压测数据仍保留，清理属于删除数据操作，需要单独确认。
- 后端仓库仍有本轮无关未跟踪文档 `docs/jisou-bot-functions.md`、`docs/telegram-search-development-plan.md`、`docs/telegram-search-large-scale-architecture.md`，本轮未处理。
- 本轮未做真实云关机/删机/IP 释放，只通过数据库构造和计划接口验证调度语义。
