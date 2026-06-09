# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-10 00:24 CST
- 状态：已修复 IP 删除计划和 IP 删除历史记录混淆的问题。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 前端分支：`codex/cloud-asset-list-performance`
- 目标主分支：`main`

## 本轮背景

- 用户指出：IP 删除计划和 IP 删除记录不对，删除计划应是待执行，删除记录应是已经执行。
- 复查发现生命周期查询层存在 `completed active` 中间口径：
  - “实例已删除且固定 IP 保留中”的资产会从 IP 删除计划中扣除。
  - 同时这类资产会被计入 IP 删除历史记录。
- 这会把“固定 IP 保留等待释放”的待执行资产误当成已执行记录。

## 修复内容

- `cloud/lifecycle_plan_queries.py`
  - 移除 `completed_unattached_ip_active_*` 查询和计数。
  - IP 删除计划只表示仍存在、未终态、等待释放的未附加固定 IP 资产。
  - IP 删除历史只来自：
    - 终态 `CloudIpLog` 记录。
    - 已删除、已终止或明确云端不存在的终态资产。
  - `ip_delete_count` 直接等于待执行 IP 删除计划数，不再扣除固定 IP 保留中的活跃资产。
  - `ip_delete_history_count` 不再叠加固定 IP 保留中的活跃资产。
- `bot/api.py`
  - 移除旧 `completed_active_count` 参数传递和包装函数。
  - 计划页接口完全按查询层的新分表契约返回。
- `cloud/tests.py`
  - 新增回归测试：实例已删除但固定 IP 保留中的资产必须留在 IP 删除计划，不得出现在 IP 删除历史记录。
  - 更新历史分页测试调用，删除旧 `completed_total` 参数。

## 验证

通过：

```bash
uv run python -m py_compile cloud/lifecycle_plan_queries.py bot/api.py cloud/tests.py
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_retained_ip_after_server_delete_stays_in_ip_delete_plan cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_separate_ip_delete_plan_and_history_items cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_counts_all_ip_delete_plans_beyond_loaded_limit cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_counts_all_ip_delete_history_beyond_loaded_limit cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_ip_delete_history_pagination_contract cloud.tests.CloudServerServicesTestCase.test_ip_delete_history_page_sources_reverse_tail_keeps_order --settings=shop.settings --verbosity=1
DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_scheduled_unattached_ip_delete_writes_log_and_history_item cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_log_without_known_note_shows_history cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_include_ip_delete_history_item cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_include_real_released_retained_ip_history_without_active_row cloud.tests.CloudServerServicesTestCase.test_unattached_ip_delete_items_expose_missing_confirmation_state --settings=shop.settings --verbosity=1
uv run python manage.py check
git diff --check
```

结果：

- 编译通过。
- IP 删除计划/记录聚焦测试 11 条通过。
- Django 系统检查通过。
- diff 空白检查通过。
- SQLite 聚焦测试仍输出既有 `db_comment/db_table_comment` 告警，不属于本轮问题。

## 结论

- IP 删除计划现在只表示待执行释放的未附加固定 IP。
- IP 删除历史记录现在只表示已执行或已终态的删除事实。
- “服务器已删除但固定 IP 保留中”不会再被下沉到历史记录，会留在 IP 删除计划等待释放。
