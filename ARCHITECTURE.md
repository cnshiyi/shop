# 架构规划

## 当前目标
后端正在从历史分散 app 收口到更清晰的五层结构，目标是把运行时真实实现稳定压到：`shop / core / bot / orders / cloud`。

## 当前事实结构
- `shop/`：Django 项目配置层
- `core/`：公共配置、公共模型与基础设施
- `bot/`：Telegram 用户模型、后台认证/用户/配置 API、机器人交互
- `orders/`：充值、余额流水、商品、购物车、订单与交易相关服务
- `cloud/`：云套餐、价格模板、云订单、云资产、服务器、监控模型与相关服务/缓存
- `tron/`：链上扫描与资源巡检，运行时已优先依赖 `cloud/`、`orders/`、`bot/`

## 旧层现状
以下旧目录的运行时职责已经全部迁入新域，并已从当前工作树删除：
- `accounts/`
- `finance/`
- `mall/`
- `monitoring/`
- `dashboard_api/`

补充说明：后台聚合路由已并回 `shop/dashboard_urls.py`；`biz/` 已删除，相关测试已迁入 `cloud/tests.py`。

其中：
- `bot.models`、`orders.models`、`cloud.models` 已是当前真实模型归属。
- 旧 `accounts/finance/mall/monitoring` 目录本体已删除，相关模型/服务/admin/命令职责都已并入新域。
- 旧 `dashboard_api` 已退出运行时并并回 `shop/dashboard_urls.py`。

## 迁移策略
- 保持 `db_table` 稳定，避免为代码收口额外改线上数据结构。
- 保留历史 migration 链，通过 `SeparateDatabaseAndState` 前移 Django state。
- 某段新实现一旦迁移并验证通过，就立即删除旧实现、旧 helper、旧转发层和无用导入。

## 当前剩余重点
### 阶段 A：继续压缩旧入口
- 继续清理 `biz` 仅剩的测试命名空间与历史兼容口径
- 继续清理 `dashboard_api` 已退场后的旧文档口径
- 清理 README / 架构文档中的旧结构叙述

### 阶段 B：完成 `INSTALLED_APPS` 收口
- `accounts` / `finance` / `mall` / `monitoring` 已全部退出运行时配置
- `dashboard_api` / `biz` 也已退出运行时 app 集
- 当前重点转为清理测试命名空间与历史文档口径

## 当前判断
运行时层面的旧 app 收口已经完成。

剩余需要注意的风险点不再是运行时代码，而是历史迁移文档与测试命名空间：
- 历史 migration 文件仍会保留旧 app label 作为历史链记录
- 测试已迁入新域，例如 `cloud/tests.py`
- 文档仍需持续避免把已删除目录写成“仍保留”
