# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-10 21:50 CST
- 状态：已按用户要求停止将云服务器创建/重建进度提示抄送管理。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 前端分支：未改动
- 目标主分支：`main`

## 本轮背景

- 用户反馈“云服务器创建/重建仍在执行中”的机器人编辑消息会高频抄送管理。
- 用户要求这类进度提示不再抄送管理。

## 修改内容

- `bot/handlers.py`
  - 新增云服务器后台任务进度提示识别逻辑。
  - 在 `_copy_user_notice_to_admins()` 入口过滤这类进度提示。
  - 过滤覆盖普通发送和编辑消息两条抄送路径，用户本人仍会收到进度消息。
- `bot/tests.py`
  - 新增回归测试，覆盖“云服务器创建/重建仍在执行中”编辑消息不触发管理员抄送。

## 验证

通过：

```bash
git diff --check
uv run python -m py_compile bot/handlers.py bot/tests.py
uv run python manage.py check
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test bot.tests.TelegramListenerPushTestCase.test_notice_copy_skips_cloud_task_progress_notice --keepdb --noinput --verbosity 1
```

## 风险和下一步

- 本轮只过滤云服务器后台任务进度提示，不影响创建成功、失败、续费结果等最终结果抄送。
- 如果后续还有其他高频中间态消息刷管理群，应继续按具体文案加入抄送过滤。
