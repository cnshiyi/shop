# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 01:08 CST
- 状态：完成固定巡检清单中的只读专项审计，未发现需要本轮立即落地的安全热修。
- 本轮范围：后端当前工作树保护性避让；`CloudAsset.actual_expires_at` 唯一到期事实复查；旧计划快照/旧退款入口扫描；Telegram 回调链和 64 字节限制实测。

## 覆盖结果

- 工作树保护：
  - 后端已有未提交改动：`cloud/api_asset_snapshots.py`、`cloud/models.py`、`cloud/tests.py`、`cloud/migrations/0059_dashboard_snapshot_group_due_order_indexes.py`、`output/`。
  - 本轮未修改这些文件，避免把自动化文档记录和用户业务改动混入同一补丁。
- 资产到期事实复查：
  - 运行时代码仍以 `CloudAsset.actual_expires_at` 为资产到期事实。
  - 扫描未发现订单侧 `service_expires_at` 或订单侧 `actual_expires_at` 被重新当作运行时主事实回流。
  - `cloud/api_orders.py` 中出现的 `actual_expires_at` 仍是资产事实的透传/编辑入口，未把事实重新落回订单表。
- 旧入口与兼容逻辑复查：
  - 未发现废弃 runtime app 回流。
  - 未发现旧退款函数名重新接入当前主链路。
  - 发现 `core.persistence`、`cloud.dashboard_snapshots` 等“snapshot”命名仍是当前仪表盘统计/缓存实现，不属于被禁用的旧计划快照表回流。
- Telegram 回调链复查：
  - 极端长链回调现有压缩逻辑仍生效，资产详情、二级动作、自动续费等高风险回调均保持在 64 字节以内。
  - 直接实测样本：
    - `cad:999999999999999999:d:999999999999999999` 长度 43 字节。
    - `au:999999999999999999:a:999999999999999999:999999999999999999` 长度 61 字节。
    - `ao:999999999999999999:a:999999999999999999:999999999999999999` 长度 61 字节。

## 发现与处理

- 本轮未发现需要代码修复的新回归。
- 一次误选测试名导致 `AttributeError`，已改用仓库内现有 `RetainedIpRenewalUiTestCase` 聚焦用例重新验证，结果通过；未对测试代码做任何修改。

## 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase.test_asset_detail_callback_from_extreme_order_detail_stays_under_limit bot.tests.RetainedIpRenewalUiTestCase.test_asset_detail_callback_recompacts_nested_asset_detail_back_path bot.tests.RetainedIpRenewalUiTestCase.test_extreme_nested_cloud_callbacks_stay_under_telegram_limit --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py shell -c "from bot.keyboards import cloud_asset_detail_callback, append_back_callback, cloud_auto_renew_callback; samples=[('asset_detail_from_extreme_order', cloud_asset_detail_callback(999999999999999999, 'cloud:detail:999999999999999999:profile:orders:cloud:filter:provisioning:page:999999999999999999')), ('asset_detail_nested_asset', cloud_asset_detail_callback(999999999999999999, 'cad:999999999999999999:d:999999999999999999:o:provisioning:999999999999999999')), ('asset_action_upgrade', append_back_callback('cloud:aa:upgrade:999999999999999999', 'cloud:ad:asset:999999999999999999:cloud:list:page:999999999999999999')), ('auto_renew_on', cloud_auto_renew_callback('on', 999999999999999999, 'cloud:ad:asset:999999999999999999:cloud:list:page:999999999999999999'))]; [print(name, len(value.encode()), value) for name, value in samples]"
git diff --check
```

## 红线

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥、云厂商密钥、完整代理链接、代理 secret 或登录密码。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款逻辑、旧退款函数名或旧兼容入口。

## 剩余风险与下一轮

- 后端当前有用户未提交业务改动，下一轮继续前先确认这些改动是否已稳定；自动化不应跨越式插入相关代码修复。
- 若要继续高风险路径覆盖，优先回到真实浏览器或数据库对账场景，补做计划页/代理列表深分页真页与数据库精确排序一致性核验。
