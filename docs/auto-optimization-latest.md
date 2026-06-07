# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-07 21:13 CST
- 状态：修复生命周期计划页未反映全局关机 / 删机 / IP 删除总开关的展示层回归。
- 本轮范围：生命周期计划总开关联动、计划页状态展示、聚焦测试补齐。

## 修复摘要

- `bot/api.py` 的生命周期计划展示层现在显式叠加三个总开关：
  - `cloud_server_shutdown_enabled=0` 时，关机计划项输出 `global_shutdown_disabled`。
  - `cloud_server_delete_enabled=0` 时，删机计划项输出 `global_server_delete_disabled`。
  - `cloud_ip_delete_enabled=0` 时，IP 删除计划项输出 `global_ip_delete_disabled`。
- 上述状态会同步落到 `queue_status`、`plan_state`、`plan_state_label`、`blocked_reason`，避免执行器已拦截但后台仍显示“待执行”的口径错位。
- `cloud/tests.py` 新增总开关联动测试，并给原有单项开关测试补上显式总开关前置条件，避免默认配置掩盖测试意图。

## 发现

- 本轮从固定巡检清单的“生命周期全局开关 / 单项开关联动”专项审计入手。
- 审计发现：生命周期执行层会受 `cloud_server_shutdown_enabled`、`cloud_server_delete_enabled`、`cloud_ip_delete_enabled` 拦截，但 `/api/admin/tasks/plans/` 的计划项构造和展示态主要只看资产单项开关。
- 结果是总开关关闭时，后台任务计划页仍可能把真实不可执行项显示成“待执行”或普通“计划中”，不利于排障和值班判断。

## 压测 / 数据规模

- 本轮为展示层一致性最小修复，没有新增 10 万级以上压测或真实浏览器翻页。
- 真实可验证范围改为生命周期计划 API 的总开关联动聚焦测试，避免在没有明确新页面变更时重复做高成本页面巡检。

## 验证

本地已通过：

```bash
UV_CACHE_DIR=/private/tmp/uv-cache-shop DJANGO_TEST_SQLITE=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_use_stage_specific_asset_switches cloud.tests.CloudServerServicesTestCase.test_lifecycle_plans_show_global_stage_switches --settings=shop.settings --verbosity=1
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python manage.py check
UV_CACHE_DIR=/private/tmp/uv-cache-shop uv run python -m py_compile bot/api.py cloud/tests.py
git diff --check
git -C /Users/a399/Desktop/data/vue-shop-admin diff --check
```

结果：2 个生命周期计划总开关 / 单项开关联动聚焦测试、Django 系统检查、编译检查和前后端空白检查均通过。SQLite 输出的 `db_comment` 警告为已知数据库能力差异。

## 红线

- 本轮未执行真实云资源创建、删除、关机、释放 IP、换 IP、真实支付、链上广播、删除业务数据、删除测试库或生产发布。
- 本轮未打印密钥、私钥、Telegram session、TOTP、支付密钥或云厂商密钥。
- 本轮未恢复废弃 runtime app、订单侧到期字段、旧计划快照、旧退款逻辑、旧退款函数名或旧兼容入口。

## 剩余风险

- 任务计划页现在能正确暴露总开关阻塞态，但本轮没有做新的真实浏览器点击验证；通知计划页、任务中心页和生命周期计划深分页仍值得继续做页面级巡检。
- 当前工作区仍有不属于本轮的脏改动：`docs/real-machine-test-report.md`、`.playwright-cli/`。
- 当前没有 `logged_in` 状态的 Telegram 登录账号，机器人真机菜单 / 回调验证仍受限。
