# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 19:18 CST
- 状态：完成通知计划摘要构建回归修复，消除 `_build_notice_plan_summary` 对关键字专用参数 `now` 的错误位置传参。
- 后端 Commit：待提交。
- 前端 Commit：本轮无前端变更，`/Users/a399/Desktop/data/vue-shop-admin` 工作区仍为干净状态。

## 本轮覆盖范围

- 后端仓库：`/Users/a399/Desktop/data/shop`
- 前端仓库：`/Users/a399/Desktop/data/vue-shop-admin`
- 巡检方式：`TODO.md` 已无未完成显式任务，本轮按 `docs/auto-optimization-control.md` 执行只读巡检并领取一个最小安全修复。
- 重点入口：
  - 通知计划摘要构建。
  - 通知计划 10 万级分页相关回归。

## 发现并修复的问题

- 问题：`cloud/api_tasks.py` 中 `_build_notice_plan_summary()` 调用 `_notice_group_summary_page_from_rows()` 时，把 `now` 作为位置参数传入。
- 风险：`_notice_group_summary_page_from_rows()` 的 `now` 是关键字专用参数；该调用路径在真实执行时会触发 `TypeError`，导致通知计划摘要构建失败。
- 修复：
  - 保持通知计划分组行只查询一次，复用同一轮 `due_rows`/`future_rows` 计算分页结果和总数统计。
  - 使用 `now=now` 关键字传参，确保摘要构建路径可执行。
  - 新增回归测试 `test_notice_plan_summary_reuses_group_rows_for_counts`，同时校验总数统计复用同一轮分组结果。

## 本轮验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_allows_deep_offsets_beyond_100k cloud.tests.CloudServerServicesTestCase.test_notice_plan_summary_reuses_group_rows_for_counts cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_counts_all_future_groups_beyond_loaded_limit cloud.tests.CloudServerServicesTestCase.test_notice_task_detail_deep_group_page_has_no_duplicates --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/api_tasks.py cloud/tests.py
git diff --check
```

补充说明：

- SQLite 测试库仍会输出既有 `db_comment/db_table_comment` 警告，不属于本轮回归。
- 本轮未执行前端构建检查，因为前端仓库无改动，且修复范围仅限后端通知计划摘要逻辑。

## 压测与真实操作

- 本轮不新增压测数据，沿用上轮 10 万级通知计划分页结论做代码回归验证。
- 本轮未执行真实云资源创建、关机、删机、释放 IP、换 IP、真实支付、链上广播或生产发布。
- 本轮未打印密钥、Telegram session、TOTP、支付密钥、云厂商密钥或登录 token。

## 受限项

- `docs/real-machine-test-report.md` 当前存在未提交用户改动，本轮不覆盖、不提交。

## 下一步

- 优先继续通知计划 10 万级查询耗时优化，目标是降低深页 `4s+` 级耗时。
- 继续按自动巡检要求复查生命周期总开关与资产单项开关联动。
