# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-09 11:47 CST
- 状态：已修复 Telegram 用户 ID 没复用的问题。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 目标主分支：`main`

## 本轮背景

- 用户在后台用户列表看到新用户 ID 为 `92`，而低位 ID `1`、`2` 等仍存在明显空洞，指出“ID 没复用”。
- 经默认本地库核查，`bot_deleted_telegram_user_slot` 当前为空，因此新用户没有可消费槽位，只能走自增。
- 这暴露了历史删除或非后台删除留下的 ID 空洞无法复用的问题。

## 修复内容

- `bot/services.py`
  - 保留原有删除槽位优先复用逻辑。
  - 当 `DeletedTelegramUserSlot` 为空时，新增兜底扫描：查找 `bot_user.id` 最小缺口，并优先用该 ID 创建新 Telegram 用户。
  - 收掉原先嵌套整事务的复用创建方式，改用保存点包住指定 ID 创建，避免复用失败时污染外层事务。
- `bot/tests.py`
  - 新增无槽位空洞复用测试：已有 `id=1、2、5` 且槽位为空时，新用户必须复用 `id=3`。
  - 保留删除接口写槽位后新用户复用旧 ID 的测试。

## 实测

- 默认本地库核查：
  - 修复前：`DeletedTelegramUserSlot.objects.count() == 0`。
  - 修复前最小空洞：`id=1`。
- 默认本地库实际创建测试用户：
  - 调用 `_get_or_create_user_sync(990000000003, 'codex_reuse_gap_verify', 'ID复用验证')`。
  - 创建结果：`user_id=1`，证明无槽位时会复用最小空洞。
  - 实测后已删除该测试用户，避免污染列表。

## 验证命令

通过：

```bash
uv run python -m py_compile bot/services.py bot/tests.py
uv run python manage.py check
DJANGO_TEST_SQLITE=1 uv run python manage.py test bot.tests.DashboardCloudAccountVerifyTestCase.test_delete_user_unbinds_assets_and_new_user_reuses_deleted_id bot.tests.DashboardCloudAccountVerifyTestCase.test_new_user_reuses_lowest_missing_id_without_slot --settings=shop.settings --verbosity=1
uv run python manage.py shell -c '默认本地库无槽位空洞复用实测'
git diff --check
```

结果：

- Django 系统检查通过。
- 后端相关文件编译通过。
- 删除槽位复用测试通过。
- 无槽位空洞复用测试通过。
- 默认本地库实测新用户复用 `id=1` 通过，测试用户已清理。
- SQLite 聚焦测试仍输出既有 `db_comment/db_table_comment` 能力差异告警，不属于本轮问题。

## 结论

- 以后后台删除留下的槽位会优先复用。
- 如果历史上没有槽位但 `bot_user.id` 有空洞，新 Telegram 用户也会优先复用最小空洞 ID。
- 用户截图里这类 `1、2、92` 的断层场景，后续新用户会先补低位空洞，而不是继续自增。
