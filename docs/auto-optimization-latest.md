# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 22:24 CST
- 状态：已修复任务中心生命周期分区对重叠 DB 任务和计划总数的重复累计问题。
- 后端提交：本轮代码与记录已整理，待提交。
- 前端提交：本轮无前端代码变更。

## 本轮覆盖范围

- 后端仓库：`/Users/a399/Desktop/data/shop`
- 前端仓库：`/Users/a399/Desktop/data/vue-shop-admin`
- 重点链路：
  - 后台任务中心 `lifecycle` 分区
  - 生命周期计划总数 `full_plan_total`
  - 生命周期 DB 任务 `CloudLifecycleTask`
  - 生命周期计划预览项和 DB 任务重叠去重
  - 任务中心失败/进行中计数口径

## 本轮发现

- 任务中心生命周期分区原逻辑会先取生命周期计划总数，再把重叠的 DB 任务条目额外叠加到 `total/active`。
- 当同一台资产或同一订单同时存在“计划总数中的生命周期项”和“已落库的 `CloudLifecycleTask`”时，任务中心会把同一对象算两次。
- 这会导致任务中心摘要数字大于真实待处理对象数，尤其在待执行或失败重试任务存在时更明显。

## 本轮修复

- 在 `cloud/task_center.py` 新增 `_lifecycle_db_task_matches_active_plan()`，按任务类型判断 DB 任务是否已经落在当前生命周期计划集合里。
- 生命周期分区统计时，先扣除与当前计划集合重叠的 DB 任务，再回加 DB 任务本身，避免 `full_plan_total` 与 DB 任务双算。
- 新增回归测试，覆盖“生命周期计划总数为 1，且同一对象已有 pending DB 任务”时 `total/active` 不得变成 `2`。

## 压测/数据规模

- 聚焦验证规模：`cloud.tests_task_center` 共 `17` 项测试。
- 新增回归覆盖：`1` 项，专门验证生命周期计划总数与 DB 任务重叠时不重复累计。
- 本轮未执行 10 万/50 万分页压测，也未执行真实云资源、真实支付或 Telegram 真机交互。

## 本轮验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py test cloud.tests_task_center
git diff --check
```

说明：

- 直接使用默认 MySQL 测试库在当前沙箱会因 `127.0.0.1` 连接限制失败，因此聚焦测试改用仓库内置 `DJANGO_TEST_SQLITE=1` 模式执行。
- SQLite 测试输出包含 `db_comment/db_table_comment` 能力差异告警，但 `cloud.tests_task_center` 17 项测试全部通过。
- 本轮没有执行真实关机、删机、IP 释放、支付、链上广播、生产发布或删除业务数据。
- `docs/real-machine-test-report.md` 当前仍存在既有未提交真实机器测试记录，本轮不覆盖、不提交。

## 结论

- 任务中心生命周期分区的摘要计数口径已与对象去重逻辑对齐。
- 同一生命周期对象在“计划总数”和“DB 任务”同时存在时，不再被重复计入 `total/active`。
- 本轮修复仅影响任务中心聚合展示口径，不改生命周期执行器行为。

## 剩余风险

- 生命周期分区预览项当前仍主要展示关机/删机计划，对 IP 删除项的预览覆盖仍可继续补强。
- 默认 MySQL 测试在当前沙箱不可直连，涉及真实库口径的更深对账仍需在可连环境继续跑。
- 真实云资源创建后的完整关机、删机、IP 释放闭环仍需继续在授权范围内逐项验证。
- 机器人多任务高并发真机点击压测仍未完成；需要 Telegram Bot API 和 MTProto 恢复可连后继续。
- 当前仍有既有真机报告脏文件 `docs/real-machine-test-report.md`，需要单独处理，不应混入本轮巡检记录提交。
