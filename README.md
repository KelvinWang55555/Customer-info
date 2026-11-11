# 上海自动化产业每日监控 (sh-automation-daily)

每天早上自动汇总两类信息并推送到你的**个人微信**：

1. **上海新发布的自动化产业投资项目/政策公示** —— 抓取上海市经信委等政府网站公示页，按关键词过滤
2. **新注册的自动化相关企业线索** —— 免费网络搜索聚合（可选升级为企查查 API）

架构：**GitHub Actions 每天定时运行 → 采集 → 关键词过滤 + 去重 → 生成日报 → Server酱 推送到个人微信**。

纯 Python 标准库实现，**无需安装任何依赖**。

---

## 一、快速上手（5 步）

### 1. 获取 Server酱 SendKey（个人微信即可，无需企业微信）

1. 打开 https://sct.ftqq.com/ ，用**微信扫码**登录
2. 点「SendMessage」页 → 复制你的 **SendKey**（形如 `SCTxxxxxxxxxxxx`）
3. 按页面提示**关注微信公众号「方糖」**——消息就是通过它推给你的
4. （可选）在 Server酱「通道配置」里把消息通道设为微信服务号，确保消息在微信里收到

### 2. 推送代码到你的 GitHub 新仓库

```bash
cd sh-automation-daily
git add -A
git commit -m "init: 上海自动化产业每日监控"
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

### 3. 配置 Secrets

仓库页面 → **Settings → Secrets and variables → Actions → New repository secret**：

| Secret 名 | 值 | 必填 |
|---|---|---|
| `SERVERCHAN_SENDKEY` | Server酱 SendKey（推荐，个人微信） | ✅ 必须 |
| `PUSHPLUS_TOKEN` | PushPlus token（备选渠道） | 可选 |
| `WECOM_WEBHOOK` | 企业微信群机器人 Webhook（备选渠道） | 可选 |
| `QCC_API_KEY` | 企查查开放平台 Key | 可选 |
| `QCC_SECRET_KEY` | 企查查 SecrectKey | 可选 |

> 脚本按 SERVERCHAN_SENDKEY → PUSHPLUS_TOKEN → WECOM_WEBHOOK 的顺序自动选择渠道，配了哪个用哪个。

### 4. 启用 Actions 并手动试跑一次

仓库 → **Actions** 标签页 → 若提示 "Workflows aren't being run"，点 **Enable**。
选择 **daily-sh-automation-report** → **Run workflow** 手动触发一次，验证能收到微信消息。

### 5. 完成

之后 **每天北京时间 08:30** 自动运行（GitHub Actions 定时有 ±15 分钟左右误差，属正常）。
每日报告会存档在 `reports/` 目录，去重状态在 `state/seen.json`（由 Actions 自动提交回仓库）。

---

## 二、怎么改监控范围

全部配置在 **`config/sources.json`**，改完 push 即生效：

- **`gov_notices`**：政府公示页列表。想加别的部门/区（如浦东新区科经委），照格式加一个 URL 即可；解析器是通用的，页面抓不到时只会在"数据源状态"里记 ⚠️，不影响其他源。
- **`automation_keywords`**：标题命中任一关键词才算"自动化相关"，按需增删。
- **`search_companies.queries`**：新注册企业线索的搜索词，按需调整。
- **`report.push_empty`**：设为 `false` 则当天无新增时不推送。

---

## 三、关于企查查（可选升级）

免费搜索渠道只能拿到网页里提到的注册信息，**覆盖不全、时点不精确**。企查查官方 API 可精确查询"上海 30 天内新注册的自动化相关企业"，按你的需求明显更对口：

1. 开通企查查开放平台账号（https://openapi.qcc.com/ ，选企业信息模糊搜索类接口）
2. 把 Key/SecrectKey 配置成上面的两个 Secrets
3. 在 `app/collectors/qcc_api.py` 里按你所购接口的文档微调 API 地址和返回字段解析（当前是常见结构的模板）

---

## 四、本地运行（可选）

```bash
python -m app.main --dry-run   # 只采集生成报告，不推送、不更新状态
python tests/test_parse.py     # 离线解析测试
python -m app.main             # 完整运行
```

## 五、已知限制

- GitHub Actions 定时任务不是秒级精确，可能延迟几分钟到半小时。
- 政府网站偶尔改版或抽风，个别源抓取失败会在日报"数据源状态"里标注，不会中断整体。
- Bing 搜索结果的地区/时间由搜索引擎决定，新注册企业线索按天去重但不保证当日全覆盖。
- Server酱免费版每天最多推 5 条消息（本任务每天只发 1 条，足够）；若当天无新增且 `report.push_empty` 为 false 则不发。
- state/seen.json 需要写权限：workflow 里已配 `permissions: contents: write`，仓库若是私有也没有问题。
