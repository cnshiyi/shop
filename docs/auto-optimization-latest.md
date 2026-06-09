# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-09 11:04 CST
- 状态：已修复用户列表 10 万级分页/搜索性能、补齐前端分页跳页显示，并重做聊天记录页面观感。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 前端分支：`codex/cloud-asset-list-performance`

## 本轮背景

- 用户反馈用户数据不能完全显示，只能显示 5 页。
- 用户要求实际测试用户删除后 ID 能否复用。
- 用户反馈聊天记录页面观感太差，要求实际截图查看。
- 用户补充要求用户数据压测 10 万条，并测试搜索。

## 修复内容

- `bot/api_users.py`
  - 用户列表分页改为数据库侧 `offset/limit` 分页，按 `id desc` 稳定排序。
  - 代理数只对当前页用户计算，不再对搜索结果全量取 ID 后内存排序，避免 10 万级数据下列表和搜索被拖慢。
- `bot/tests.py`
  - 扩展用户列表分页测试，覆盖第 1 页、第 2 页、最后页不重复不丢。
  - 新增文本搜索和数字搜索分页测试。
- `/Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src/views/dashboard/users/index.vue`
  - 分页器显示 `共 X 条 / Y 页`。
  - 开启快速跳页、页大小选择和完整页码展示。
  - 表格关键列不换行，避免 ID/TG ID 被折断影响扫描。
- `/Users/a399/Desktop/data/vue-shop-admin/apps/web-antd/src/views/dashboard/telegram-accounts/chats.vue`
  - 去掉大面积 hero、渐变和说明式文案。
  - 改为紧凑后台双栏布局：左侧统计/搜索/会话列表，右侧消息区/回复输入。
  - 空态和输入框文案收敛为后台工具用语。

## 10 万用户压测

- 压测库：`/private/tmp/shop_users_100k.sqlite`
- 数据规模：`bot_user` 100000 条。
- 隔离策略：使用 `DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_users_100k.sqlite`，未写入默认本地业务库。
- 清理策略：压测库位于 `/private/tmp`，不提交仓库；需要释放空间时可直接删除该文件。

压测结果：

- 第 1 页，20 条：`11.41ms`，总数 100000，总页数 5000，无重复。
- 第 2 页，20 条：`10.89ms`，无重复。
- 第 2500 页，20 条：`14.15ms`，无重复。
- 第 5000 页，20 条：`18.31ms`，无重复。
- 精确用户名搜索 `load_user_099999`：`16.39ms`，命中 1 条。
- 前缀搜索 `load_user_09`：`9.06ms`，命中 10000 条，总页数 500。
- 数字 TG ID 搜索 `990000099999`：`25.67ms`，命中 1 条。

## 删除 ID 复用实测

- 独立压测库中实际执行：创建用户 -> 带 Bearer session 调用后台删除接口 -> 新 Telegram 用户走服务创建入口。
- 结果：删除接口返回 200；删除后写入 `DeletedTelegramUserSlot`；新用户创建后复用旧 `bot_user.id`；复用后槽位被消费。

## 页面验证

- 已用 Playwright + 系统 Chrome 打开本地 `http://127.0.0.1:5666/admin/users`。
  - 页面成功进入后台，未跳登录。
  - 分页显示 `共 500318 条 / 50032 页`，可见深页跳页输入。
  - 前端实际搜索 `real_lifecycle_20260608232809` 成功加载结果。
- 已用 Playwright 打开本地 `http://127.0.0.1:5666/admin/telegram-accounts/chats`。
  - 页面成功进入后台，未跳登录。
  - 旧文案 `Telegram Inbox`、`Enter 发送`、`先选择左侧用户` 均已消失。
  - 控制台 0 error / 0 warning。
- 截图：
  - `output/playwright/users-page.png`
  - `output/playwright/users-search-page.png`
  - `output/playwright/telegram-chats-page.png`

## 验证命令

通过：

```bash
uv run python manage.py check
uv run python -m py_compile bot/api_users.py bot/tests.py
DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.DashboardCloudAccountVerifyTestCase.test_users_list_uses_server_pagination_total_and_distinct_pages bot.tests.DashboardCloudAccountVerifyTestCase.test_users_list_searches_numeric_and_text_keywords_with_pagination bot.tests.DashboardCloudAccountVerifyTestCase.test_delete_user_unbinds_assets_and_new_user_reuses_deleted_id --settings=shop.settings --verbosity=1
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_users_100k.sqlite uv run python manage.py migrate --noinput
DB_ENGINE=sqlite SQLITE_NAME=/private/tmp/shop_users_100k.sqlite uv run python manage.py shell -c '10 万用户造数、分页、搜索、删除复用实测'
pnpm --filter @vben/web-antd typecheck
git diff --check
git -C /Users/a399/Desktop/data/vue-shop-admin diff --check
```

结果：

- Django 系统检查通过。
- 后端相关文件编译通过。
- 用户分页、搜索、删除 ID 复用聚焦测试通过。
- 10 万用户独立库压测和搜索对账通过。
- 前端 `vue-tsc` 类型检查通过。
- 前后端 `git diff --check` 通过。
- SQLite 迁移和测试仍输出既有 `db_comment/db_table_comment` 能力差异告警，不属于本轮问题。

## 剩余风险

- 默认本地库仍有历史压测数据约 50 万条，本轮没有清理默认本地库。
- 用户列表当前按 `id desc` 稳定排序，不再按代理数优先排序；这是为 10 万级分页准确性和性能做出的口径收敛。
