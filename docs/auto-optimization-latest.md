# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-08 16:58 CST
- 状态：完成代理列表更多分组标签深页/末页真实前端巡检，修复前端 Typography ellipsis warning，并复测机器人多任务高并发。
- 后端 Commit：本轮记录随本轮提交一起保存。
- 前端 Commit：本轮前端修复单独提交在 `/Users/a399/Desktop/data/vue-shop-admin`。

## 本轮覆盖范围

- 后端仓库：`/Users/a399/Desktop/data/shop`
- 前端仓库：`/Users/a399/Desktop/data/vue-shop-admin`
- 重点：
  - 代理列表云资源视图分组分页更多标签覆盖
  - 前端控制台 warning 清理
  - 数据库 distinct 分组数和接口分页 key 对账
  - 机器人返回链和多任务高并发回归

## 本轮发现

- 真实前端多标签分组巡检中，代理列表能正确显示数据，但控制台仍有：
  - `Warning: [ant-design-vue: Typography] When ellipsis is enabled, please use content instead of children`
- 问题来自代理列表表格中多个 `TypographyParagraph` 同时使用 `ellipsis` 和子文本。
- 该 warning 不影响数据加载，但会污染上线前控制台质量，也容易掩盖真正的前端错误。

## 本轮修复

前端：

- 文件：`/Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src/views/dashboard/cloud-assets/index.vue`
- 将代理列表表格中带 `ellipsis` 的纯文本 `TypographyParagraph` 改为使用 `:content`。
- 覆盖分组表格和普通表格两套重复渲染区：
  - 用户摘要
  - 云资源名称
  - 资源 ID
  - 用户名标签
  - 实例 ID / 云资源 ID
  - 代理链接
  - 备注

后端：

- 本轮后端业务代码未改。
- 更新本文件和 `docs/refactor-version-record.md` 记录巡检结果。

## 真实前端验证

实际打开：

```text
http://127.0.0.1:5666/admin/cloud-assets
```

本轮真实点击覆盖：

- 用户分组 / 全部 / 第 `1` 页：`20` 组，分页 `共 2489996 个用户/分组`。
- 用户分组 / 已过期 / 第 `1` 页、第 `5000` 页：均 `20` 组。
- 用户分组 / 异常待确认 / 第 `1` 页、第 `5000` 页：均 `20` 组。
- 用户分组 / 云账号异常 / 第 `1` 页、第 `1000` 页：均 `20` 组。
- 用户分组 / 未绑定用户 / 第 `1` 页、第 `5000` 页：均 `20` 组。
- 群组分组 / 未绑定用户 / 第 `1` 页：`20` 组。
- 群组分组 / 未绑定群组 / 第 `1` 页、第 `5000` 页：均 `20` 组。
- 群组分组 / 续费关闭 / 第 `1` 页、第 `1000` 页：均 `20` 组。

修复后复测：

- 分组页面仍显示 `20` 组、`20` 行。
- 分页显示 `共 2489996 个用户/分组`。
- 控制台 error/warning：`0`。
- 业务 API 失败：`0`。
- Vite 热更新模块请求仍可能出现 `ERR_ABORTED`，属于开发服务器模块切换噪音。

## 数据库对账

本轮对 5 个抽样深页做接口和数据库对账：

- 用户分组 / 异常待确认 / 第 `5000` 页：
  - API total `100000`
  - DB distinct total `100000`
  - API 分组 key 与 DB 分页 key 一致
- 用户分组 / 云账号异常 / 第 `1000` 页：
  - API total `1145001`
  - DB distinct total `1145001`
  - API 分组 key 与 DB 分页 key 一致
- 用户分组 / 未绑定用户 / 第 `5000` 页：
  - API total `100001`
  - DB distinct total `100001`
  - API 分组 key 与 DB 分页 key 一致
- 群组分组 / 未绑定群组 / 第 `5000` 页：
  - API total `100003`
  - DB distinct total `100003`
  - API 分组 key 与 DB 分页 key 一致
- 群组分组 / 续费关闭 / 第 `1000` 页：
  - API total `101002`
  - DB distinct total `101002`
  - API 分组 key 与 DB 分页 key 一致

## 机器人高并发

继续复测：

- 通知复制并发隔离。
- 钱包直付 / 钱包补付同时执行。
- `60` 路批量后台任务隔离。
- 订单详情、资产详情、IP 查询、自动续费返回链和 `callback_data <= 64` 字节。

结果：聚焦测试通过，未发现任务串线、返回链污染或后台任务隔离问题。

## 验证

通过：

```bash
pnpm -F @vben/web-antd typecheck
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py makemigrations --check --dry-run
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.TelegramListenerPushTestCase.test_notice_copy_wrapper_keeps_concurrent_user_sends_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_high_concurrency_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_background_tasks_keep_bulk_concurrency_isolated bot.tests.RetainedIpRenewalUiTestCase.test_cloud_server_list_order_detail_uses_short_back_callback bot.tests.RetainedIpRenewalUiTestCase.test_asset_detail_callback_from_extreme_order_detail_stays_under_limit bot.tests.RetainedIpRenewalUiTestCase.test_asset_detail_callback_recompacts_nested_asset_detail_back_path bot.tests.RetainedIpRenewalUiTestCase.test_cloud_ip_query_actions_return_to_query_menu bot.tests.RetainedIpRenewalUiTestCase.test_cloud_auto_renew_callbacks_keep_nested_back_under_limit --settings=shop.settings --verbosity=1
git diff --check
```

红线扫描通过。命中项为既有测试桩账号字符串、Telegram 登录账号 API 文件名，以及 `CloudServerOrder.ip_recycle_at` 同步记录，不是旧订单到期事实回流。

## 清理

- 已删除本轮临时后台登录用户 `codex巡检_frontend_probe`。
- 已删除 `/private/tmp/shop_frontend_probe_token.txt`。

## 受限项

- 本轮未执行真实云资源创建、关机、删机、释放 IP、换 IP、真实支付、链上广播、生产发布或删除业务数据。
- 本轮未打印密钥、Telegram session、TOTP、支付密钥、云厂商密钥或完整代理链接。

## 下一步

- 继续巡检生命周期计划、通知计划和代理列表之间的统计口径是否再次分叉。
- 继续把机器人返回链、`callback_data <= 64` 字节限制和多任务高并发作为固定回归项。
