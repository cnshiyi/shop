# Shop 自动优化最新状态

本文件由自动化每轮覆盖更新，供快速复查当前状态。详细历史记录继续追加到 `docs/refactor-version-record.md`。

## 最近一轮

- 时间：2026-06-11 01:15 CST
- 状态：已按用户要求实际创建资源测试 AWS 全区域，移除不可用区域 `ap-southeast-3`。
- 后端分支：`codex/cloud-asset-lifecycle-refactor`
- 前端分支：未改动
- 目标主分支：`main`

## 本轮背景

- 用户要求“测试全部区域，如果有不支持的区域在代码里移除”，随后明确要求“实际创建资源”。
- 本轮只测试 AWS Lightsail 区域可创建性，不安装代理、不分配固定 IP；成功创建的实例立即删除。
- 本轮不修改生命周期计划页、通知计划或代理列表页面。

## 真机测试结果

- 云账号：后台 AWS 云账号 `#55`
- 测试方式：每个区域创建 1 台测试实例，进入 `running` 后立即删除。
- 初始套餐：`nano_3_0`
- 镜像：`debian_12`

`nano_3_0` 创建成功区域：

- `ap-northeast-1`
- `ap-northeast-2`
- `ap-southeast-1`
- `ca-central-1`
- `eu-central-1`
- `eu-north-1`
- `eu-west-1`
- `eu-west-2`
- `eu-west-3`
- `us-east-1`
- `us-east-2`
- `us-west-2`

复核结论：

- `ap-south-1`：`nano_3_0` 不存在，但区域专属 `nano_3_1` 创建成功，保留区域。
- `ap-southeast-2`：`nano_3_0` 不存在，但区域专属 `nano_3_2` 创建成功，保留区域。
- `ap-southeast-3`：`CreateInstances` 和 `GetBundles` 均返回 `UnrecognizedClientException`，确认当前账号不可用，移除代码区域入口。
- `ap-southeast-5`：同样不可用，但原本不在 AWS 业务区域表内，无需移除。
- 残留复核：按 `codex-region-` 前缀扫描可访问区域，测试实例残留数量为 `0`。

## 修改内容

- `cloud/services.py`
  - 从 `AWS_REGION_NAMES` 移除 `ap-southeast-3`，避免购买/换 IP 区域入口继续展示不可用 AWS 区域。
  - 新增 AWS 不可用区域屏蔽集合，价格区域规范化和 AWS 区域拉取都会跳过 `ap-southeast-3` / `ap-southeast-5`，避免后续同步重新带回。
- `bot/keyboards.py`
  - 从 `_COMPACT_REGION_CODES` 移除 `ap-southeast-3` 的 callback 压缩映射。
- `cloud/tests.py`
  - 新增回归测试，确认价格区域规范化会过滤 AWS 不可用区域。
- `docs/real-machine-test-report.md`
  - 追加 AWS 全区域真实创建资源测试报告，资源 ID、实例名和公网 IP 已脱敏。
- `docs/refactor-version-record.md`
  - 追加本轮中文版本记录。

## 验证

通过：

```bash
git diff --check
uv run python -m py_compile cloud/services.py bot/keyboards.py cloud/tests.py
uv run python manage.py check
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test cloud.tests.CloudServerServicesTestCase.test_aws_unsupported_regions_are_filtered_from_price_regions --keepdb --noinput --verbosity 1
DJANGO_TEST_REUSE_DB=1 uv run python manage.py test bot.tests.RetainedIpRenewalUiTestCase.test_extreme_nested_cloud_callbacks_stay_under_telegram_limit bot.tests.RetainedIpRenewalUiTestCase.test_cloud_change_ip_keyboards_keep_back_path --keepdb --noinput --verbosity 1
```

## 风险和下一步

- 本轮实际创建并删除了多台 AWS Lightsail 测试实例；报告显示无测试实例残留。
- `ap-south-1` 和 `ap-southeast-2` 需要区域专属 bundle 才能创建，后续如同步套餐，应避免用单一区域 bundle 直接套用到所有 AWS 区域。
