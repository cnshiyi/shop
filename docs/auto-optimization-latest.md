# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 02:10 CST
- 状态：完成一轮生命周期/机器人只读巡检，并修复 `SiteConfig` 在 `SimpleTestCase` 场景下误打数据库异常日志的问题。
- 本轮范围：后端生命周期总开关/单项开关联动、通知计划屏蔽逻辑、机器人资产详情/订单详情/续费/换 IP/重装/修改配置返回链；前端仓库只检查 git 状态，未改动代码。

## 发现与修复

- 发现 1：机器人 UI 聚焦测试 `RetainedIpRenewalUiTestCase` 运行时，按钮配置读取会触发 `SiteConfig.get()`，在 `SimpleTestCase` 禁止数据库访问场景下误记录 `SiteConfig.get 读取失败` 栈日志。
  - 影响：不影响功能结果，但会污染机器人高频回归输出，降低真实异常信号密度。
  - 修复：`core/models.py` 新增 `DatabaseOperationForbidden` 识别；测试隔离场景下直接返回默认值并降级为 debug 跳过，不再记录 error 栈。
  - 回归：`core/tests.py` 新增 `SiteConfigSimpleTestIsolationTestCase`，验证该场景返回默认值且无 error 日志。

## 本轮巡检结论

- 生命周期计划：
  - `test_lifecycle_plans_use_stage_specific_asset_switches` 通过，确认关机、删机、IP 删除分别读取对应单项开关。
  - `test_lifecycle_plans_show_global_stage_switches` 通过，确认总开关关闭时页面状态、标签和阻塞原因一致。
- 通知计划：
  - `test_notice_task_detail_hides_shutdown_disabled_lifecycle_notices` 通过，确认生命周期关闭后不会误暴露相关通知项。
- 机器人返回链：
  - `RetainedIpRenewalUiTestCase` 49 个聚焦测试通过，覆盖资产详情、订单详情、续费、钱包异步任务、换 IP、重装、修改配置、返回上一层和 callback 64 字节限制。
- 前端仓库：
  - `/Users/a399/Desktop/data/vue-shop-admin` 本轮 `git status --short` 为空，未发现待处理前端工作树改动。

## 压测与数据规模

- 本轮未新增 10 万级以上压测数据，也未执行新的浏览器真分页压测。
- 数据规模：沿用上一轮已验证的高数据基线，不修改真实业务数据，不执行真实云资源操作。

## 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile core/models.py core/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test core.tests.SiteConfigSimpleTestIsolationTestCase bot.tests.RetainedIpRenewalUiTestCase cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_use_stage_specific_asset_switches cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_show_global_stage_switches cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_hides_shutdown_disabled_lifecycle_notices --settings=shop.settings --verbosity=1
git diff --check
```

SQLite `db_comment` 警告仍是已知数据库能力差异。

## 红线

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款逻辑、旧退款函数名或旧兼容入口。
- 本轮未打印 Telegram token、Telegram session、TOTP、支付密钥、云厂商密钥、完整代理链接或代理 secret。

## 下一步

- 继续做真实浏览器专项巡检，优先覆盖通知计划、服务器删除历史和任务中心在高数据下的翻页/跳页/控制台状态。
- 继续真机 Telegram 多任务高并发点击验证，重点覆盖购买、续费、换 IP、重装、修改配置和返回链。
