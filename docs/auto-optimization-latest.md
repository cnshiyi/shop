# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-09 13:17 CST
- 状态：已移除机器人购买数量页空套餐说明行。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 前端分支：`codex/cloud-asset-list-performance`
- 目标主分支：`main`

## 本轮背景

- 用户反馈机器人购买服务器数量选择页显示 `套餐说明: 无`，要求移除。
- 该文案位于云服务器定制购买数量页，涉及选择套餐后进入数量页，以及数量分页返回数量页两个入口。

## 修复内容

- `bot/handlers.py`
  - 新增 `_plan_description_line(plan)`，优先读取 `display_description`，其次读取 `plan_description`。
  - 套餐说明为空时返回空字符串，不再显示 `套餐说明: 无`。
  - 套餐说明非空时继续显示 `套餐说明: ...`。
  - 两个购买数量页入口统一复用该 helper，避免文案口径分叉。

## 验证

通过：

```bash
uv run python -m py_compile bot/handlers.py
uv run python manage.py check
rg -n "套餐说明: .*无|套餐说明" bot/handlers.py
git diff --check
```

结果：

- `bot/handlers.py` 编译通过。
- Django 系统检查通过。
- 搜索确认不再存在 `套餐说明: 无` 兜底文案。
- diff 空白检查通过。

## 结论

- 机器人购买数量页中，套餐说明为空时不会再显示该行。
- 有真实套餐说明时仍会显示，避免丢失有效说明。
