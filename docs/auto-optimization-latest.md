# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-06 14:47 CST
- 状态：已完成 IP 删除历史和服务器删除计划高数据压测，修复计划页全量计数、缓存加载性能和前端分页误导问题。
- 本轮范围：生命周期计划 API、IP 删除计划/历史全量计数、生命周期计划统计缓存、计划页标题/分页显示、真实浏览器翻页复测。

## 修改内容

- 生命周期计划 API：
  - `ip_delete_count` 改为全库活动未附加固定 IP 删除计划总数，不再使用当前加载行数。
  - `ip_delete_history_count` 改为全量历史来源总数，不再被 `limit` 截断。
  - “实例已删除但固定 IP 保留中”的活动行按展示规则转入历史计数，避免计划/历史口径混淆。
  - 新增计划统计缓存快照：强制刷新时精确计算全量统计，普通加载复用缓存，避免每次页面加载都扫描 150 万资产和 50 万日志。
  - `refresh_lifecycle_plan_view` 返回全量统计，不再返回当前构造列表长度。
- 计划页前端：
  - 关机计划、删除计划、IP 删除计划、IP 删除历史标题改为 `已加载 X / 总 Y`。
  - 分页器显示“已加载 X 条”，明确当前分页只覆盖已加载数据，避免误以为本地分页就是几十万条的服务端深分页。

## 压测数据

- 当前本地压测库：
  - `CloudAsset` 总量：1500000。
  - `CloudIpLog` 总量：515739。
  - `CODEX-IPDEL-MILLION-*` 未附加固定 IP 计划资产：499999。
  - `CODEX-IPDEL-HISTORY-*` IP 删除历史日志：500000。
  - `CODEX-SERVER-PLAN-MILLION-*` 服务器删除计划资产：500000。
- 当前 API 全量计数：
  - `shutdown_plan_count=953489`。
  - `server_delete_count=954747`。
  - `ip_delete_count=500000`。
  - `ip_delete_history_count=500007`。

## 性能结果

- 优化前：
  - 强制刷新约 `19.901s`。
  - 缓存读取仍约 `13.0s`，主要耗时在每次重复扫描全量统计。
- 优化后：
  - 强制刷新仍需精确扫库，约 `20.281s`。
  - 普通缓存加载降到 `0.371s`、`0.362s`、`0.352s`。
  - 计数保持一致，列表仍按 `limit=50` 返回：服务器删除计划 50 行、IP 删除计划 50 行、IP 删除历史 50 行。

## 真实前端复测

- 真实浏览器进入 `/admin/tasks/plans` 成功，接口均返回 200。
- 页面显示：
  - `关机计划（已加载 50 / 总 953489）`
  - `删除计划（已加载 50 / 总 954747）`
  - `IP删除计划（已加载 50 / 总 500000）`
  - `IP删除历史记录（已加载 50 / 总 500007）`
  - 摘要 `IP删除历史 500007 条`
- 实际点击 IP 删除历史第 2 页：
  - 第 1 页尾部包含 `CODEX-IPDEL-HISTORY-499980`。
  - 第 2 页切换后显示 `CODEX-IPDEL-HISTORY-499979` 到 `CODEX-IPDEL-HISTORY-499960`。
  - 页面不空、不丢当前已加载数据。
- 浏览器 console：`Errors: 0`。

## 最近验证

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile bot/api.py cloud/tests.py
DJANGO_TEST_SQLITE=1 UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_counts_all_ip_delete_plans_beyond_loaded_limit cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_counts_all_ip_delete_history_beyond_loaded_limit cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_reuses_cached_count_snapshot_after_refresh cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_separate_ip_delete_plan_and_history_items --settings=shop.settings --verbosity=2
DB_ENGINE=mysql UV_CACHE_DIR=/private/tmp/uv-cache-shop PYTHONDONTWRITEBYTECODE=1 uv run python manage.py check
/Users/a399/.homebrew/bin/pnpm -C /Users/a399/Desktop/data/vue-shop-admin --filter @vben/web-antd typecheck
```

结果：编译通过；4 条聚焦测试通过；MySQL `manage.py check` 通过；前端 typecheck 通过。

## 红线

- 本轮未执行真实云资源创建、删除、关机、释放 IP、换 IP、真实支付、链上广播、删除业务数据或生产发布。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥或云厂商密钥。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照或旧退款入口。

## 剩余风险

- 本地 150 万资产和 50 万 IP 删除历史压测数据仍保留，清理需要单独确认。
- 计划页当前是“加载更多 + 本地分页已加载数据”，不是每张表独立服务端深分页；如需直接跳到第 1000 页，应继续补每张表的服务端分页参数和 API。
- 强制刷新仍约 20 秒；上线前建议由后台计划任务定时刷新缓存，前端默认走缓存读取。
