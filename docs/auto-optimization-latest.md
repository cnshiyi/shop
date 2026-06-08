# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 17:20 CST
- 状态：完成后台任务中心生命周期/通知计划总数统计修复，并完成真实前端、深页对账、机器人高并发与红线扫描复测。
- 后端 Commit：`ff069f5 fix: correct task center plan totals`
- 前端 Commit：无。

## 本轮覆盖范围

- 后端仓库：`/Users/a399/Desktop/data/shop`
- 前端仓库：`/Users/a399/Desktop/data/vue-shop-admin`
- 重点：
  - 任务中心生命周期计划统计口径
  - 任务中心通知计划统计口径
  - IP 删除计划计数缓存
  - 任务中心聚焦测试

## 本轮发现

- `cloud/task_center.py` 的任务中心总览对生命周期计划和通知计划都使用当前预览列表长度参与 `total/active` 统计。
- 当页面只取前 8 条预览任务时，总览会把真实活跃计划总数低报成 8 条左右，和计划页、快照统计不一致。
- IP 删除计划计数没有单独缓存，任务中心读取全量统计时会重复执行较重计数查询。

## 本轮修复

- 文件：`/Users/a399/Desktop/data/shop/cloud/task_center.py`
  - 生命周期计划改为优先读取快照中的全量 `total_counts/ip_delete_total_counts`，没有快照时再回退到实时统计。
  - 任务中心 `lifecycle` 分区的 `total/active` 由“预览 8 条”改为“关机 + 删机 + IP 删除”的真实活跃总数，再叠加失败历史与后台任务表状态。
  - 通知计划分区改为请求 `include_total_counts=True`，使用 `active_user_count` 作为真实总数和活跃数基础，不再依赖预览列表长度。
- 文件：`/Users/a399/Desktop/data/shop/cloud/lifecycle_plan_queries.py`
  - 为 IP 删除计划总数增加独立缓存 key，并在清理生命周期统计缓存时一并失效，避免任务中心总览重复扫全表。
- 文件：`/Users/a399/Desktop/data/shop/cloud/tests_task_center.py`
  - 新增生命周期总数测试，覆盖“预览 8 条但真实计划 27 条”场景。
  - 新增通知计划总数测试，覆盖“预览 8 条但真实通知计划 25 条”场景。

## 压测/数据规模

- 本轮未做新一轮 10 万级写压测或前端深分页压测。
- 本轮聚焦任务中心统计修复，使用补充测试构造“预览 8 条、真实总量 25/27 条”的对账场景，验证总览不再低报。
- 继续使用当前本地大数据集做读侧验证：
  - 关机计划：`1,979,990`
  - 服务器删除计划：`2`
  - 服务器删除历史：`20,010`
  - IP 删除计划：`500,000`
  - IP 删除历史：`520,010`
  - 通知计划：`21,429` 组，通知历史：`14,960`

## 真实前端验证

实际打开：

```text
http://127.0.0.1:5666/admin/tasks/plans
http://127.0.0.1:5666/admin/tasks/notices
http://127.0.0.1:5666/admin/tasks
```

结果：

- 计划页 5 个表均正常显示，分页文本分别为：
  - 关机计划：`1-50 / 共 1979990 条`
  - 删除计划：`1-2 / 共 2 条`
  - 服务器删除历史：`1-50 / 共 20010 条`
  - IP 删除计划：`1-50 / 共 500000 条`
  - IP 删除历史：`1-50 / 共 520010 条`
- 通知计划页正常显示：
  - 通知计划：`21429` 组用户通知 / `21429` 个 IP 通知项
  - 近期计划：`3428`
  - 未来计划：`18001`
  - 历史通知分页：`14960`
- 任务中心首次发现 `/api/admin/tasks/center/` 在百万级数据下被前端中断并回退旧 `/api/admin/tasks/`。
- 修复后复测：
  - `/api/admin/tasks/center/` 返回 `200`
  - 真实页面显示任务总量 `2517685`
  - 生命周期计划 `2479992/2479992`
  - 通知计划 `21431/22437`
  - 控制台 error/warning：`0`
  - 业务 API 失败：`0`

## 数据对账

接口和服务端分页 helper 逐行对账通过：

- 关机计划：第 `1` 页、第 `2` 页、第 `1000` 页。
- IP 删除计划：第 `1` 页、第 `2` 页、第 `5000` 页。
- 服务器删除历史：第 `1` 页、第 `2` 页、第 `1000` 页。
- IP 删除历史：第 `1` 页、第 `2` 页、第 `1000` 页。
- 通知计划：第 `1` 页、第 `2` 页、第 `1000` 页。

对账字段覆盖 `id/asset_id/order_id/plan_kind/plan_stage/public_ip` 或通知分组 `id`，未发现翻页丢数据或串页。

## 验证

通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests_task_center --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile cloud/task_center.py cloud/lifecycle_plan_queries.py cloud/tests_task_center.py
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_high_concurrency_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_bulk_concurrency_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_server_list_order_detail_uses_short_back_callback bot.tests.RetainedIpRenewalUiTestCase.test_asset_detail_callback_from_extreme_order_detail_stays_under_limit bot.tests.RetainedIpRenewalUiTestCase.test_asset_detail_callback_recompacts_nested_asset_detail_back_path bot.tests.RetainedIpRenewalUiTestCase.test_cloud_ip_query_actions_return_to_query_menu bot.tests.RetainedIpRenewalUiTestCase.test_cloud_auto_renew_callbacks_keep_nested_back_under_limit --settings=shop.settings --verbosity=1
git diff --check
```

说明：

- `manage.py check` 通过。
- `cloud.tests_task_center` 16 个聚焦测试通过。
- `makemigrations --check --dry-run` 返回 `No changes detected`。
- 修改文件编译检查通过。
- 机器人 8 条多任务高并发/返回链测试通过，覆盖通知复制并发隔离、钱包直付/补付并发、60 路批量后台任务隔离、订单详情/资产详情/IP 查询/自动续费返回链和 `callback_data <= 64`。
- 红线扫描通过。命中项为既有测试桩账号字符串、Telegram 登录账号 API 文件名，以及 `CloudServerOrder.ip_recycle_at` 同步记录，不是旧订单到期事实回流。
- SQLite 测试中的 `db_comment` warning 为既有兼容性提示，不属于本轮回归。

## 受限项

- 前端仓库本轮无改动，`git status --short` 为空。
- 本轮未执行真实云资源创建、关机、删机、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未打印密钥、Telegram session、TOTP、支付密钥或云厂商密钥。
- 已删除本轮临时后台登录用户 `codex_patrol_task_probe`。
- 已删除 `/private/tmp/shop_task_probe_token.txt` 及本轮临时 API 输出文件。

## 剩余风险

- 任务中心总览目前仍依赖快照与实时查询双路径，后续如果快照字段结构变化，需要继续盯住统计字段名一致性。
- 本轮没有做真实云资源创建/关机/删机/IP 释放；生命周期执行器的不可逆动作仍只做非破坏性测试。

## 下一步

- 继续巡检任务中心页面、生命周期计划页和通知计划页之间的总数、活跃数、失败数是否完全一致。
- 继续关注生命周期总开关、单项开关与关机/删机/IP 删除联动链路的真实页面回归。
