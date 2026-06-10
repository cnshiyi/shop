# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-11 00:37 CST
- 状态：已定位美国区云服务器线上创建失败原因，完成代码修复，并完成 1 台美国区真机创建、安装、删除和固定 IP 释放。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 前端分支：未改动
- 目标主分支：`main`

## 本轮背景

- 用户要求测试创建 1 台美国服务器，并已明确确认真实云资源成本。
- 线上同类创建失败，表现为业务开通入口提示没有可用后台云账号。
- 本轮按红线只处理云账号候选和真机报告，未修改生命周期计划页、通知计划或代理列表页面。

## 失败原因

- 测试库账号 `#55` 的 AWS 凭据在 `us-east-1/us-east-2/us-west-2` 只读查询均可用。
- 但该账号 `region_hint` 仍只有 `ap-southeast-1`，业务候选函数只看 `region_hint` 时会把美国区过滤掉。
- 第一次业务开通订单 `#914` 因候选账号为空失败，且未创建真实云资源。
- 补充 `region_hint` 后，同一账号、同一业务开通入口在 `us-east-1` 创建成功，确认根因是本地账号区域候选数据过窄，不是 AWS 凭据失效。

## 修改内容

- `core/cloud_accounts.py`
  - 新增从云账号 `status_note` 提取已同步/验证地区的兜底逻辑。
  - `cloud_account_supports_region()` 在 `region_hint` 未覆盖目标地区时，会继续检查状态备注中的地区列表。
  - 线上已有“同步完成，地区 ... us-east-1 ...”但 `region_hint` 仍旧的账号，不会再被美国区购买流程误过滤。
- `cloud/tests.py`
  - 新增回归测试：`region_hint=ap-southeast-1` 但 `status_note` 已确认 `us-east-1` 时，美国区账号候选必须包含该账号。
- `docs/real-machine-test-report.md`
  - 追加本轮美国区真机测试、失败复现、修复验证和资源清理记录。

## 真机结果

- 失败复现订单：`#914` / `REALUS061016302424873`，候选账号为空，云端未创建实例。
- 成功订单：`#915` / `REALUS061016325878029`
- 地区：`us-east-1`
- 套餐：`nano_3_0`
- 资源：实例名、公网 IP、固定 IP 均已脱敏写入真机报告。
- 开通结果：AWS Lightsail 创建成功，BBR、MTProxy 主/备用/Telemt、SOCKS5 安装成功。
- SOCKS5：订单保存内容已确认包含 `https://t.me/socks?server=` 格式，未输出完整链接或密码。
- 清理结果：实例已删除，固定 IP 已释放；本地订单 `#915` 为 `deleted`，资产 `#721` 为 `deleted/is_active=False`。

## 验证

通过：

```bash
git diff --check
uv run python -m py_compile core/cloud_accounts.py cloud/tests.py
uv run python manage.py check
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_cloud_account_status_note_regions_extend_region_hint --keepdb --noinput --verbosity 1
```

## 风险和下一步

- 本轮执行过真实 AWS 创建、删除和固定 IP 释放；报告显示测试资源已清理。
- 当前修复依赖云账号最近一次 `status_note` 中存在同步/验证过的地区列表；后续也可以在云同步成功时同步刷新 `region_hint`，但本轮先做最小线上止血修复。
