# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 06:28 CST
- 状态：完成一轮任务中心/通知计划/自动续费统计口径和真实页面巡检；未发现需要修改代码的问题。
- 本轮范围：任务中心统一统计、通知计划字段开关与分页、自动续费待处理队列、真实 HTTP 接口、真实前端页面、控制台错误检查。

## 巡检结论

- 后端聚焦测试全部通过：
  - `cloud.tests_task_center` 共 `14` 个测试通过。
  - 通知计划隐藏列/未来计划计数/深页无重复/关机关闭隐藏删机通知共 `4` 个测试通过。
  - 自动续费任务中心、详情、跳过无资产到期事实、批量执行、单项执行共 `5` 个测试通过。
- 真实 HTTP 接口：
  - `/api/admin/tasks/center/` 返回 5 个分区，总任务 `37701`、active `10704`、failed `1178`、warning `178`。
  - `/api/admin/tasks/notices/?compact=1&fields=basic&limit=20&history_limit=20` 返回近期 `3428`、未来 `18001`、未来用户 `18001`，加载 20 组、20 条历史；隐藏文案列时没有构造文案预览。
  - `/api/admin/tasks/notices/?compact=1&fields=basic,text,channels&limit=20&history_limit=20` 返回同口径计数，文案列开启时有文案预览。
  - `/api/admin/tasks/auto-renew/?limit=20&history_limit=20` 返回待续费 `443`，加载 443 条待处理和 200 条历史。
- 真实前端页面：
  - `/admin/tasks` 任务列表加载到任务、自动续费入口和生命周期/计划入口。
  - `/admin/tasks/notices` 通知计划加载到标题、计数区域和表格列。
  - `/admin/tasks/auto-renew` 自动续费页面加载到标题、待执行信息和表格。
  - 页面控制台错误数：`0`。

## 验证

已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_basic_fields_skip_batch_text_payload cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_counts_all_future_groups_beyond_loaded_limit cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_deep_group_page_has_no_duplicates cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_hides_shutdown_disabled_lifecycle_notices --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_task_center_counts_pending_auto_renew_retry_tasks cloud.tests.CloudServerServicesTestCase.test_auto_renew_task_detail_includes_due_retry_and_fallback_items cloud.tests.CloudServerServicesTestCase.test_auto_renew_detail_ignores_order_without_asset_expiry_fact cloud.tests.CloudServerServicesTestCase.test_run_auto_renew_tasks_executes_due_retry_and_fallback_queue cloud.tests.CloudServerServicesTestCase.test_run_auto_renew_order_executes_single_order --settings=shop.settings --verbosity=1
```

真实页面巡检使用系统 Chrome 打开：

```text
http://127.0.0.1:5666/admin/tasks
http://127.0.0.1:5666/admin/tasks/notices
http://127.0.0.1:5666/admin/tasks/auto-renew
```

说明：

- SQLite 的 `db_comment` warnings 仍是已知测试噪声。
- 临时后台 session 已删除，未保留 `/private/tmp/shop_admin_session.json`、`/private/tmp/shop_pw_state.json` 或 `.playwright-cli/` 产物。

## 受限项

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮没有新增代码问题，因此只更新巡检文档并提交记录。

## 下一步

- 继续不停轮巡检，下一轮优先做云资产同步 worker、云账号异常资产可见性、同步任务失败/重试状态和真实页面展示对账。
- 继续把机器人多任务高并发、生命周期开关执行链、代理列表标签翻页作为固定高优先级检查项。
