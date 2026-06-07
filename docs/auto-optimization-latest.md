# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 00:10 CST
- 状态：完成 IP 删除历史混合来源分页排序修复，避免历史页先吐尽日志再补资产。
- 本轮范围：生命周期计划查询层、IP 删除历史分页契约、跨来源顺序回归测试、10 万量级只读压测。

## 本轮修复

- `cloud/lifecycle_plan_queries.py`
  - `ip_delete_history_page_sources()` 改为按统一时间轴合并三类来源：
    - `CloudIpLog` 历史日志按 `created_at desc, id desc`
    - 已删除未附加 IP 资产按 `updated_at desc, id desc`
    - 完成态保留 IP 资产按 `updated_at desc, id desc`
  - 不再按“先日志、再历史资产、最后完成态资产”的来源顺序硬拼页，避免首页、跨页和深分页错序。
  - 使用分块拉取 + 小顶堆归并，只读取当前分页窗口需要的区间。
- `cloud/tests.py`
  - 新增 `test_lifecycle_plans_ip_delete_history_mixes_logs_and_assets_by_time`。
  - 覆盖日志、已删除资产、完成态保留 IP 资产交错时间时，`ip_delete_history_page=1/2` 必须保持统一时间轴顺序。

## 发现

- 修复前的 `ip_delete_history_page_sources()` 先分页日志，再分页历史资产，最后才补完成态保留 IP 资产。
- 一旦资产或完成态保留 IP 的 `updated_at` 新于部分日志，IP 删除历史就会出现：
  - 首屏顺序错误。
  - 跨页页边界错误。
  - 深分页和真实执行时间轴不一致。

## 压测

- 只读合成压测规模：`100000` 条混合历史源
  - `CloudIpLog` 40000
  - 已删除未附加 IP 资产 30000
  - 完成态保留 IP 资产 30000
- 实测页耗时：
  - `page=1 size=50`：`0.12 ms`
  - `page=2 size=50`：`0.07 ms`
  - `page=1000 size=50`：`20.29 ms`
  - `page=2000 size=50`：`38.00 ms`
- 结果：统一时间轴归并在 10 万量级合成源下仍可稳定返回整页，无重复、无丢页。

## 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_ip_delete_history_mixes_logs_and_assets_by_time cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_ip_delete_history_pagination_contract cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_history_mixes_orders_and_assets_by_updated_at cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_server_history_includes_orphan_deleted_server_asset cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_returns_server_delete_history_table --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/lifecycle_plan_queries.py cloud/tests.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
DB_ENGINE=sqlite SQLITE_NAME=:memory: UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python - <<'PY'
# 10 万量级只读合成压测，补丁 cloud.lifecycle_plan_queries.ip_delete_history_page_sources 的三源归并分页
PY
git diff --check
```

SQLite `db_comment` 警告为已知数据库能力差异，不影响本轮结果。

## 前端与页面验证

- 前端仓库 `/Users/a399/Desktop/data/vue-shop-admin` 本轮 `git status --short` 为空，无新增改动。
- 本轮未重跑浏览器页面点击；当前沙箱对本地端口监听仍有限制，上一轮记录的 `/admin/tasks/plans` 真页验证阻塞未解除。

## 红线

- 本轮未执行真实云资源创建、关机、删除服务器、释放 IP、真实支付、链上广播、生产发布、删除业务数据或删除测试库。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥、云厂商密钥、完整代理链接、代理 secret 或登录密码。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款逻辑、旧退款函数名或旧兼容入口。

## 剩余风险与下一轮

- 计划页历史查询层现在有两处相似的堆归并逻辑；后续可考虑抽公共 helper，但这不属于本轮最小修复范围。
- 下一轮优先继续审计计划页和代理列表深分页真页对账；如沙箱端口限制仍在，只能继续走 API 级和只读压测验证。
