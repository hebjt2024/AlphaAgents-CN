<div align="center">

# AlphaAgents-CN

**A 股多智能体投研系统** · 基于 [TradingAgents](https://github.com/TauricResearch/TradingAgents) 架构

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-green.svg)](pyproject.toml)
[![Upstream](https://img.shields.io/badge/upstream-TauricResearch%2FTradingAgents-orange.svg)](https://github.com/TauricResearch/TradingAgents)

</div>

> 模拟真实投研机构的多智能体协作：分析师并行作业 → 多空辩论 → 风控博弈 → 组合经理裁决。
> 专注 A 股数据源，全中文交互，**Apache 2.0 可商用**。

---

## ✨ 项目特色

### 🇨🇳 深度适配 A 股，而非简单翻译

| 能力 | 说明 |
|---|---|
| **本地行情仓库** | 日线 OHLCV 落到独立 SQLite（`data/market.db`），回测/评估/K 线/模拟盘本地优先，只补缺失区间；同一份历史只下载一次 |
| **数据质量检查** | 覆盖范围、新鲜度、OHLC 不自洽、异常涨跌幅、长期缺口；支持 API / CLI / WebUI 查看 |
| **三源容错** | AkShare → BaoStock → 本地缓存，逐级降级，网络不可用仍能出报告 |
| **智能限流** | 信号量并发控制 + 指数退避重试 + 请求超时保护 |
| **代码归一** | `600519` / `600519.SH` / `sh.600519` / `SH600519` / `920xxx` 全部识别为同一标的 |
| **沪深北全覆盖** | 主板 / 创业板 / 科创板 / 北交所，自动识别涨跌停比例与 B 股排除 |
| **交易日历** | 自动跳过周末与节假日，非交易日回退到最近交易日 |
| **资金面** | 主力资金 / 北向资金 / 龙虎榜，辅助短线择时 |
| **停牌提示** | 行情区间内缺失交易日自动标注（可能停牌/无成交/数据缺失） |
| **历史估值** | 历史分析使用分析日 PE/PB，避免把实时快照当作历史值（防未来函数） |
| **成本回测** | 内置佣金 + 印花税 + 可选滑点，贴近 A 股真实交易成本 |
| **可编排图** | 精简 / 标准 / 深度 / 自定义，按需增删辩论、交易员与风控阶段 |
| **并行分析** | 多个分析师并行执行，缩短首轮耗时；并发度可配置 |
| **快速预览** | 策略规则初判，不调用 LLM；深度分析同时后台运行 |
| **策略扫描** | 多头趋势、均线金叉、放量突破、缩量回踩、底部放量、龙头、一阳夹三阴、箱体、缠论/波浪简化、情绪周期等 15 种信号 |
| **策略族去重** | 15 个策略归并为 7 个**独立假设族**（均线金叉与 MACD 金叉触发集合完全相同、平台突破是放量突破的子集…），族内去重后再计票，不再把同一个假设数三遍 |
| **估值历史分位** | PE-TTM / PB 当前值在近 N 年历史中的百分位，直接回答"贵不贵"（28 倍 PE 对茅台是低估，对成长股可能是高估） |
| **业绩预告 / 快报** | A 股强制披露制度，预增预减是全年最强催化之一；附财报披露日历 |
| **财务排雷** | 商誉、股权质押、应收账款、经营现金流四项检查 + 0-100 风险分，A 股爆雷四大前兆 |
| **事件日历** | 解禁 / 财报披露 / 除权除息 / 业绩预告窗口合并成"未来 30 天要发生什么" |
| **限售解禁提醒** | 解禁日期、规模、占总股本比例；距解禁 <=14 天自动标为关注 |
| **持仓 ↔ 建议联动** | 分析结束后，若账户持有该标的，**报告里自动附上**加仓 / 减仓 / 清仓的**具体股数**（整手、不可做空） |
| **止损止盈提醒** | 持仓触及止损/目标、接近触发、深亏、未设止损，逐项告警 |
| **市场情绪历史** | 每日复盘落库形成时间序列，给出冰点/偏冷/中性/偏热/高潮判定与趋势；样本不足 20 天时显式标注"判定不可靠" |
| **策略回测** | 统一回测引擎：涨跌停不可成交、T+1、停牌跳空、100 股整手、滑点、最低佣金、ATR 止损；样本内/样本外切分 + 有效样本量门槛 + 显著性检验 + 多重检验校正 |
| **结构化决策** | 综合评分、置信度、参考价、止损、目标价、风险收益比、建议仓位 |
| **决策分歧可见** | 规则初判与组合经理结论的差异随结果返回，方向冲突时显式标注并暂停给出仓位 |
| **组合管理** | 账户、持仓账本、成交流水、组合快照（权重 / 浮盈 / 行业暴露）；WebUI 组合页支持盯市、绩效曲线与交易流水 |
| **组合级风控** | 单票 / 行业 / 总仓位上限、单笔风险预算、日内亏损与回撤熔断 —— 全部**代码强制**，不再只是提示词里的一句话 |
| **模拟盘** | T+1 批次锁定、涨跌停不可成交、整手约束、现金冻结；WebUI 可直接提交委托、撤单、盯市与按历史行情推进撮合；决策卡可转成委托单 |
| **评估闭环** | 决策与 5/10/20 日前瞻收益自动对齐、命中率与有效样本量；WebUI 评估页、CLI `evaluate`、每日自动评估并写回角色记忆 |
| **组合绩效** | 每日快照、累计/年化收益、最大回撤、波动率、夏普、行业暴露与风险体检；CLI `portfolio` 可查看或刷新 |
| **可观测性** | 每次运行的 token 用量、成本估算、逐阶段耗时落库；`/api/runs` 查询，WebUI 设置页展示聚合指标 |
| **访问鉴权** | 可选 API Key（`WEBUI_API_KEY`）、按 IP 限流、请求 ID 贯穿日志 |
| **任务队列** | 有界 worker 队列、原子认领、可取消、僵尸任务回收、优雅退出；调度器可脱离 WebUI 用 cron 独立运行 |
| **盘中实时行情** | 全市场快照、1/5/15/30/60 分钟线、五档盘口；自动判断交易时段 | WebUI / API |
| **盘中告警** | 价格上穿/下穿、涨跌幅、量比、换手率、成交额，以及持仓止损/止盈；冷却去重、可确认、可推送 | WebUI / API / CLI / Scheduler |
| **全市场选股** | 全 A 快照 + 最新业绩报表 + 可选资金流；价值/质量/成长/动量/流动性/资金流多因子打分，风格权重可切换，候选池每日归档 | WebUI / API / CLI / Scheduler |
| **公告 / 研报 / 股东** | 公告按利好利空与影响级别分类；研报评级分布、盈利预测、最近研报；股东户数趋势与前十大股东；全部可作为 LLM 工具 | WebUI / API / CLI / LLM |
| **真实社媒舆情** | 东财人气榜、概念热度、微博情绪、雪球热度（可选）；综合热度/情绪评分与异常升温/降温；每日快照形成时间序列 | WebUI / API / CLI / LLM / Scheduler |
| **宏观 / 轮动 / 两融 / ETF** | PMI、CPI、PPI、M2、LPR、Shibor、中美国债收益率；两融余额变化；行业今日/5日/10日轮动；ETF 主力资金流 | WebUI / API / CLI / LLM |
| **组合风险与再平衡** | 年化波动、最大回撤、VaR/CVaR、Beta、跟踪误差、集中度、风险贡献；大盘/行业/个股压力测试；等权/风险平价/修复超限的调仓股数 | WebUI / API / CLI |
| **LLM 可靠性** | 结构化决策 JSON（置信度/论点/引用/风险标记）、外部数据不可信隔离与注入检测、单次成本/token 预算硬约束 | 图编排 / API / 运行指标 |
| **多用户与权限** | 登录会话、admin / analyst / viewer 角色、风险揭示确认、写操作审计日志；默认单用户模式不受影响 | WebUI / API / CLI |
| **自选股预警** | 只保留触发策略的标的，支持手动/定时推送到企业微信 / 飞书 / 通用 Webhook |
| **市场复盘** | 指数、涨跌家数、涨跌停、行业/概念热度、行业资金流、涨停行业分布与 Markdown 复盘 |
| **板块历史** | 同花顺行业 1/5/20 日涨跌幅、成交额与 60 日走势图 |
| **每日简报** | 复盘 + 自选股预警 + 明日关注，支持定时推送与归档 |
| **任务 API** | `job_id`、状态、结果、取消、删除，方便外部自动化轮询 |
| **定时任务管理** | WebUI/API 添加、暂停、删除每只股票的定时分析 |
| **股票搜索** | 代码/名称自动补全；涨停池热门股按连板与封板资金排序 |
| **离线资源** | ECharts / marked / DOMPurify 本地化，不依赖 CDN |
| **联网搜索** | 可选 Tavily，补充新闻、公告、政策与事件 |
| **主题切换** | WebUI 支持浅色 / 深色主题 |
| **名称解析** | 自动解析股票简称 → 代码，含别名与常用股票映射 |
| **全中文** | Prompt、CLI、WebUI 全部中文，附零基础教学文档 |

### 🧠 多智能体辩论架构

```
              ┌─ 基本面分析师 ─┐
              ├─ 技术分析师 ───┤
   START ─────┼─ 新闻分析师 ───┼──→ 多头研究员 ⇄ 空头研究员
              ├─ 社交舆情 ─────┤         （辩论 N 轮）
              └─ 主力资金 ─────┘              ↓
                                        研究总监（裁决）
                                              ↓
                                     交易员（方案）
                                              ↓
                        激进 ⇄ 中性 ⇄ 保守  （三方风控辩论）
                                              ↓
                                        组合经理（最终裁决）
                                              ↓
                                            END
```

分析师并行作业 → 多空辩论 → 三方风控辩论 → 最终裁决，
由 LangGraph 状态图编排，辩论轮次与路由逻辑显式可控。

### 🖥️ 双形态界面

- **Web 工作台**：FastAPI + WebSocket 实时推送 Agent 进展；
  工作台 / 报告中心 / 设置三视图，内置可缩放 K 线（1M/3M/6M/1Y/3Y/全部，滚轮缩放、拖动平移、MA 与成交量）、
  资金流柱状图、回测资金曲线、协同研判阶段可视化；K 线会按分析日期截断，避免展示未来行情
- **CLI**：交互式终端，支持历史报告浏览、回测与自选股管理

### 🧩 自定义图编排

WebUI 的“分析编排”支持按需组合分析流程：

| 预设 | 流程 |
|---|---|
| 精简 | 核心分析师 → 最终裁决（速度最快、成本最低） |
| 标准 | 分析师 → 多空辩论 → 交易员 → 风险辩论 → 最终裁决 |
| 深度 | 标准流程 + 更多辩论轮次 |
| 自定义 | 自选分析师、是否启用多空辩论 / 交易员 / 风险辩论 |

策略扫描是流程中的第一个独立阶段，WebUI 的研判进度图会展示本次启用的策略组合；
最终裁决（Portfolio Manager）始终保留，确保一定输出 `final_trade_decision`。

---

## 🚀 快速开始

### 方式一：uv 隔离环境（推荐）

```bash
git clone https://github.com/hebjt2024/AlphaAgents-CN.git
cd AlphaAgents-CN

# 创建项目内虚拟环境（不会污染全局 Python）
uv venv --python 3.11
uv sync --extra dev

# 启动 Web 工作台
uv run python webui/run_webui.py --port 8000
```

> uv 会在项目内创建 `.venv`，避免与全局环境中的 `parsel` / `jmespath` / 阿里云 SDK
> 等依赖互相冲突。所有命令都可以用 `uv run ...` 执行，例如
> `uv run pytest`、`uv run ruff check .`、`uv run alphaagents analyze`。

### 方式二：Web 工作台（pip）

```bash
git clone https://github.com/hebjt2024/AlphaAgents-CN.git
cd AlphaAgents-CN

# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置模型 API Key
cp .env.example .env
# 编辑 .env，填入至少一个 API Key

# 3. 启动
python webui/run_webui.py --port 8000
```

访问 http://localhost:8000

> 默认只监听 `127.0.0.1`。需要局域网访问时显式使用 `--host 0.0.0.0`，
> 并强烈建议同时设置 `WEBUI_API_KEY` 或开启多用户模式（Docker 镜像内已显式绑定 0.0.0.0）。

### 方式三：Docker（镜像打包 / 部署）

```bash
# 1. 配置（填写 LLM 密钥）
cp .env.docker.example .env

# 2. 构建并启动
docker compose up -d --build

# 3. 验证
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/readyz
```

- 国内网络构建失败时，在 `.env` 里设置 `APT_MIRROR` / `PIP_INDEX_URL`，
  或使用本机代理：`pwsh scripts/docker-build.ps1 -Proxy http://127.0.0.1:7890`
  （脚本会自动转换成容器可访问的 `host.docker.internal`）。
- 多架构构建 / 推送镜像仓库 / 服务器部署 / 反向代理 / 备份恢复 / 排障，
  见 **[docs/DOCKER.md](docs/DOCKER.md)**。
- CI 会在推送 `v*` 标签时自动构建多架构镜像并发布到 GHCR
  （`.github/workflows/docker.yml`）。

### 方式四：命令行（pip）

```bash
pip install -r requirements.txt
cp .env.example .env

# 交互式分析
alphaagents analyze

# 查看历史报告
alphaagents history

# 回看某次分析
alphaagents view 600519 2024-01-10

# 轻量回测（默认计入 A 股佣金与印花税）
alphaagents backtest 600519 --start 2023-01-01 --end 2024-12-31

# 策略扫描（默认核心组合，也可指定策略或 --all）
alphaagents strategies 600519
alphaagents strategies 600519 --strategies bull_trend,volume_breakout
alphaagents strategies 600519 --all

# 全市场选股 / 盘中告警
alphaagents screen --top 30
alphaagents alerts add 600519 --type price_above --threshold 1500
alphaagents alerts check

# 自选股管理
alphaagents watch add 600519 --name 贵州茅台
alphaagents watch list

# 任务记录管理（查看 / 清理历史任务）
alphaagents jobs list
alphaagents jobs clean --dry-run
alphaagents jobs clean
alphaagents jobs purge --before 2026-11-01
```

### 依赖冲突排查（jmespath / 阿里云 SDK）

若安装时出现：

```text
aliyun-python-sdk-core requires jmespath<1.0.0, but you have jmespath 1.1.0
```

这是 `parsel` 1.11+ 与阿里云 SDK 的版本冲突。本项目已不再依赖 `parsel`；
如果你机器上还有其他项目需要 `parsel`，把版本降到 1.10.x 即可同时兼容：

```bash
pip install "parsel>=1.10,<1.11" "jmespath>=0.9.3,<1.0.0"
pip check
```

如果没有其他项目依赖 `parsel`，也可以直接 `pip uninstall -y parsel`，
再安装 `jmespath<1.0.0`。

---

## ⚙️ 配置

### 模型服务

支持 OpenAI、Anthropic、Google、OpenRouter 及任何 OpenAI 兼容端点。
默认值从 `.env` 读取，未配置时使用代码内默认值：

```bash
TRADINGAGENTS_LLM_PROVIDER=anthropic
TRADINGAGENTS_DEEP_THINK_LLM=MiniMax-M3
TRADINGAGENTS_QUICK_THINK_LLM=MiniMax-M3.1-Flash
TRADINGAGENTS_BACKEND_URL=https://api.minimax.cn/anthropic
# TRADINGAGENTS_MAX_DEBATE_ROUNDS=1
# TRADINGAGENTS_MAX_RISK_DISCUSS_ROUNDS=1
```

WebUI 的 **分析设置 → 模型配置** 可以临时覆盖提供商、模型名和 API Base URL；
点击“应用 .env 默认”可恢复 `.env` 中的配置。CLI 与定时任务使用 `.env` 默认值。

> 深度思考与快速思考分离是本项目的性能设计：分析师并行阶段跑在快模型上，
> 只有辩论与裁决环节使用强模型，可显著降低 token 成本。

### 分析耗时优化

- 默认开启分析师并行：市场 / 舆情 / 新闻 / 基本面同时执行
- 并发度可调：

```bash
TRADINGAGENTS_PARALLEL_ANALYSTS=1
TRADINGAGENTS_PARALLEL_ANALYSTS_WORKERS=4
```

- 如果模型或数据源限流，可改为 `WORKERS=2` 或 `PARALLEL_ANALYSTS=0` 回退串行
- 快速查看可使用“精简”流水线（2 个分析师，跳过辩论/交易员/风控）
- 点击“开始分析”会先显示**快速预览**（策略规则初判，不调用 LLM），深度结果完成后自动替换
- 长分析建议使用任务 API 启动后离开页面，稍后查询结果

### 联网搜索（可选）

配置 Tavily 后，新闻/舆情分析师可以联网补充最新新闻与公告：

```bash
TAVILY_API_KEY=你的TavilyKey
```

未配置时相关工具会返回明确提示，不影响其他分析流程。

### 策略扫描

WebUI“策略选择”可勾选本次分析要使用的策略（默认核心 6 项，不必每次跑全部 15 个）；
“快捷工具 → 策略扫描”支持按当前选择扫描，并可批量扫描自选股；
“策略回测”会统计信号触发后的 5/10/20 日收益与胜率，并给出含 A 股成本的简单跟随/避险净值。

分析完成后会生成**结构化决策卡**：综合评分、置信度、参考价、止损参考、目标参考、
风险收益比和建议仓位。该卡基于策略共识 + ATR 规则估算，仅供参考，不构成投资建议。

### 自选股策略预警

“策略扫描 → 触发预警”只保留至少触发一个策略的自选股，并列出触发策略、方向、
强度和理由；“推送预警”会把同样内容发送到已配置的 Webhook：

```bash
WECOM_WEBHOOK_URL=企业微信机器人地址
FEISHU_WEBHOOK_URL=飞书机器人地址
GENERIC_WEBHOOK_URL=通用 JSON Webhook 地址
```

未配置任何渠道时，预警仍会在页面展示，不会报错。

定时预警：

```bash
SCHEDULE_ENABLED=1
SCHEDULE_ALERTS=1
SCHEDULE_TIME=18:00
```

每个交易日收盘后自动扫描自选股；只有出现触发信号时才推送，避免无信号噪音。

### 市场复盘

WebUI“快捷工具 → 市场复盘”会生成当日盘后快照：

- 主要指数：上证、深证、创业板、科创50、沪深300、中证500
- 市场情绪：上涨/下跌/平盘家数、涨跌停、真实涨跌停、活跃度
- 行业与概念热度：领涨/领跌板块、成交额、领涨股
- 行业资金流：净流入/净流出居前、流入/流出金额
- 涨停行业分布
- 板块热度历史：选择同花顺行业，查看 1/5/20 日涨跌幅和 60 日走势图
- 一键复制 Markdown 复盘

数据为 AkShare 实时快照，接口异常时会保留已获取部分并给出提示。
API：

- `GET /api/market/review?date=YYYY-MM-DD`（日期仅用于提示，不回溯历史）
- `GET /api/market/sectors`
- `GET /api/market/sector-history?name=半导体&days=60`

盘后自动推送：

```bash
SCHEDULE_ENABLED=1
SCHEDULE_REVIEW=1
SCHEDULE_TIME=18:00
```

每个交易日收盘后生成复盘并发送到已配置的企业微信 / 飞书 / 通用 Webhook；
未配置渠道时只记录日志，不影响其他定时任务。

### 每日盘后简报

“快捷工具 → 每日盘后简报”会把市场复盘、自选股触发预警和“明日关注”合并成一份
Markdown 简报，支持复制、归档和推送。报告中心新增“每日简报”归档列表，
同一天重复归档会覆盖；定时推送也会自动归档。

定时推送：

```bash
SCHEDULE_ENABLED=1
SCHEDULE_BRIEFING=1
SCHEDULE_TIME=18:00
```

`SCHEDULE_BRIEFING=1` 时优先发送合并简报，不再单独发送 `SCHEDULE_ALERTS` /
`SCHEDULE_REVIEW`，避免重复通知。

归档 API：

- `POST /api/daily-briefing/archive`
- `GET /api/briefings`
- `GET /api/briefings/{id}`
- `DELETE /api/briefings/{id}`

### 任务化 API 与搜索

- `POST /api/analyze` 现在返回 `job_id`
- `GET /api/jobs/{job_id}` / `GET /api/jobs/{job_id}/result` 可轮询任务
- `POST /api/jobs/{job_id}/cancel` 取消任务
- `GET /api/scheduled` / `POST /api/scheduled` / `PATCH /api/scheduled/{id}` / `DELETE /api/scheduled/{id}` 管理定时分析
- `GET /api/preview?ticker=600519` 规则化快速预览
- `GET /api/market/stock-search?q=茅台` 股票自动补全
- `GET /api/market/hot-stocks` 涨停池热门股

### 组合、风控与运行指标

- `GET /api/portfolio` 账户持仓、组合权重与风险体检
- `GET /api/portfolio/performance` 每日快照绩效：累计/年化收益、最大回撤、波动率、夏普
- `POST /api/portfolio/refresh` 用全市场快照刷新持仓最新价
- `POST /api/portfolio/snapshot` 记录当日组合快照
- `GET /api/portfolio/transactions` 成交流水
- `POST /api/portfolio/evaluate` 把决策卡放到组合语境下重新评估，给出真正可执行的仓位
- `GET /api/portfolio/alerts` 持仓止损 / 止盈 / 深亏 / 未设止损提醒
- `GET /api/position/advice?ticker=600519` 结合持仓给出加仓 / 减仓 / 清仓的具体股数
- `GET /api/portfolio/risk` 年化波动、最大回撤、VaR/CVaR、Beta、集中度、压力测试与再平衡
- `POST /api/portfolio/rebalance` 等权 / 风险平价 / 仅修复超限，输出具体调仓股数
- `GET /api/runs` / `GET /api/runs/aggregate` 最近运行的 token / 成本 / 耗时

```bash
uv run alphaagents risk
uv run alphaagents risk --method risk_parity
uv run alphaagents risk --benchmark 510300
```

模拟盘：

- `GET /api/paper` 模拟账户、持仓、委托与净值曲线
- `POST /api/paper/reset` 新建/重置模拟盘
- `POST /api/paper/order` 提交模拟委托；`POST /api/paper/cancel` 撤单
- `POST /api/paper/from-plan` 把决策卡转成模拟委托
- `POST /api/paper/mark` 按最新价盯市；`POST /api/paper/advance` 按历史日线逐日撮合

推荐评估：

- `GET /api/evaluation` 历史推荐的 5/10/20 日前瞻收益、命中率与有效样本量
- `POST /api/evaluation/run` 手动评估并可选写回角色记忆
- `GET /api/evaluation/report/{id}` 单条报告的后续结果

CLI：

- `alphaagents evaluate --ticker 600519 --limit 20`
- `alphaagents portfolio --refresh --snapshot`

### A 股研判（估值 / 事件 / 排雷 / 情绪）

- `GET /api/valuation/percentile?ticker=600519&years=5` 估值历史分位
- `GET /api/earnings/forecast?ticker=600519` 业绩预告与快报
- `GET /api/fundamentals/red-flags?ticker=600519` 商誉 / 质押 / 应收 / 现金流排雷
- `GET /api/events/calendar?ticker=600519&days=30` 未来事件日历
- `GET /api/market/sentiment/history?days=60` 市场情绪历史与冰点/高潮判定
- `POST /api/market/sentiment/snapshot` 立即记录一次情绪快照

这五项也会作为 LLM 工具（`get_valuation_percentile` / `get_earnings_forecast` /
`get_financial_red_flags` / `get_lockup_expiry` / `get_event_calendar`）
提供给基本面与市场分析师，分析报告里会直接引用。

### 盘中实时行情与告警

WebUI 工作台“实时行情 / 分时”可查看当前标的快照与分钟线；导航新增“告警”页：

- 规则类型：价格上穿/下穿、涨幅/跌幅超过、量比超过、换手率超过、成交额超过；
- 自动附加持仓止损/止盈提醒；
- 每条规则有独立冷却时间，避免同一条件反复刷屏；
- 事件可逐条或全部确认；
- 定时器开启盘中告警后，交易时段每 30 秒检查一次。

```bash
uv run alphaagents alerts list
uv run alphaagents alerts add 600519 --type price_above --threshold 1500
uv run alphaagents alerts check --notify
uv run alphaagents alerts events --unacked
uv run alphaagents alerts ack --all
```

API：

- `GET /api/realtime/session` 当前交易时段
- `GET /api/realtime/quote?ticker=600519` 实时快照
- `GET /api/realtime/intraday?ticker=600519&period=5&days=3` 分钟线
- `GET /api/realtime/orderbook?ticker=600519` 五档盘口
- `GET /api/alerts/summary` 规则/事件概览
- `GET/POST /api/alerts/rules` 规则列表与新增
- `PATCH/DELETE /api/alerts/rules/{id}` 启停/删除规则
- `GET /api/alerts/events` 事件列表
- `POST /api/alerts/events/{id}/ack` / `POST /api/alerts/events/ack-all` 确认
- `POST /api/alerts/check` 立即检查

盘中定时检查需要显式开启：

```bash
SCHEDULE_ENABLED=1
SCHEDULE_INTRADAY_ALERTS=1
```

### 全市场选股

WebUI 导航“选股”页：

- 风格：均衡 / 价值 / 成长 / 动量 / 质量；
- 过滤：ST、涨跌停、最小成交额、最小总市值；
- 可合并最新业绩报表（ROE / 毛利率 / 营收与净利同比 / 行业）；
- 可选合并主力资金流排行（默认关闭，接口翻页较多）；
- 可选“行业中性”：按行业均值中心化综合分，避免整条赛道一起高分；
- 结果按综合评分排序，展示各因子分、入选理由与风险标记；
- 每天的结果自动归档，可回看“昨天选了什么”。

```bash
# 默认均衡风格，Top 30，成交额≥1 亿，市值≥30 亿
uv run alphaagents screen

# 价值风格，Top 50
uv run alphaagents screen --style value --top 50

# 不合并业绩（更快，但质量/成长因子为中性）
uv run alphaagents screen --no-financials
```

API：

- `GET /api/screener` / `POST /api/screener/run` 运行选股
- `GET /api/screener/styles` 风格与权重
- `GET /api/screener/latest` 最近一次候选池
- `GET /api/screener/history` 历史运行
- `GET /api/screener/runs/{id}` 单次详情
- `DELETE /api/screener/runs/{id}` 删除归档

每日盘后自动选股（默认关闭）：

```bash
SCHEDULE_ENABLED=1
SCHEDULE_SCREENER=1
SCHEDULE_SCREENER_TOP=50
SCHEDULE_SCREENER_STYLE=balanced
```

### 宏观 / 行业轮动 / 两融 / ETF

WebUI 工作台快捷工具“宏观 / 轮动 / 两融”：

- 宏观指标：PMI、CPI、PPI、M2、LPR、Shibor、中美国债收益率与利差；
- 两融余额：全市场余额、5/20 日变化与趋势图；
- 行业轮动：今日 / 5 日 / 10 日涨跌幅、主力净流入、轮动分；
- ETF 资金流：场内 ETF 主力净流入/流出。

```bash
uv run alphaagents macro
uv run alphaagents macro --json
uv run alphaagents macro --rotation-limit 20
```

API：

- `GET /api/macro/overview`
- `GET /api/macro/margin`
- `GET /api/market/rotation`
- `GET /api/market/etf-flows`
- `GET /api/macro/dashboard`

> 东方财富板块资金流 / ETF 行情偶尔会被限流；系统会保留宏观与两融部分并给出提示。

### 真实社媒舆情

WebUI 工作台快捷工具“社媒舆情”：

- 东方财富人气榜排名（前 100）；
- 同花顺/东财概念热度（白酒、电商、茅指数等）；
- 金十微博舆情：按股票简称匹配情绪分（约 -5 ~ +5）；
- 可选雪球关注/讨论热度（接口需要 cookie，常失败）；
- 综合热度、情绪评分、冰点/偏热/过热判定；
- 与最近 20 次快照比较，标记“异常升温 / 异常降温”；
- 每日快照形成趋势图。

```bash
uv run alphaagents social 600519 --name 贵州茅台
uv run alphaagents social 600519 --snapshot
uv run alphaagents social 600519 --history
```

API：

- `GET /api/social/sentiment?ticker=600519&name=贵州茅台`
- `GET /api/social/history?ticker=600519&days=60`
- `POST /api/social/snapshot`
- `GET /api/social/hot?limit=30`

每日快照（默认关闭）：

```bash
SCHEDULE_ENABLED=1
SCHEDULE_SOCIAL=1
```

> 社媒接口只提供实时数据；历史分析日会明确返回“不可用”，避免未来函数。

### 公告 / 研报 / 股东

WebUI 工作台快捷工具“公告 / 研报 / 股东”：

- 公告按利好/利空、影响级别、主题分类；
- 研报评级分布、买入+增持占比、盈利预测、最近研报 PDF；
- 股东户数变化（筹码集中/分散）与最新前十大股东；
- 基本面分析师与新闻分析师可直接调用 `get_announcements`、
  `get_research_consensus`、`get_shareholder_changes`。

```bash
uv run alphaagents disclosure 600519
uv run alphaagents disclosure 600519 --days 90
uv run alphaagents disclosure 600519 --json
```

API：

- `GET /api/disclosure/summary?ticker=600519&days=30`
- `GET /api/disclosure/announcements?ticker=600519&days=30`
- `GET /api/disclosure/research?ticker=600519&days=180`
- `GET /api/disclosure/shareholders?ticker=600519`

### LLM 可靠性与预算

- **结构化决策块**：组合经理 prompt 要求在末尾输出 JSON，包含
  `decision / confidence / summary / key_points / risk_flags / citations`；
  解析结果随报告落库，并在报告中心展示。
- **引用要求**：关键结论需标注 `[公告]`、`[研报]`、`[行情]`、`[资金流]`、`[社媒]`。
- **提示词注入防护**：
  - 新闻、公告、研报、社媒、网页搜索统一包裹为 `<untrusted_data>`；
  - 命中“忽略以上指令 / 必须买入 / 系统提示词”等模式时加显式安全提示；
  - 所有分析师上下文附加安全约束，只把外部内容当事实材料。
- **单次预算**：

```bash
LLM_MAX_COST_PER_RUN=2.0
LLM_MAX_TOKENS_PER_RUN=800000
```

超限后在**下一次** LLM 调用前抛出 `BudgetExceededError`，停止继续烧钱。
运行指标页会显示当前预算策略。

- `GET /api/runs/budget` 查询预算策略与当前运行使用情况。

### 多用户 / 权限 / 审计

默认仍是单用户、无需登录。开启多用户：

```bash
WEBUI_MULTI_USER=1
WEBUI_SESSION_DAYS=7
# 可选；不填密码会生成随机密码写入 data/initial_admin_password.txt
WEBUI_ADMIN_USER=admin
WEBUI_ADMIN_PASSWORD=你的初始密码
```

角色：

- `admin`：全部权限，含用户管理与审计查询；
- `analyst`：可运行分析、修改自选股/组合/告警/选股等业务数据；
- `viewer`：只读，不能触发分析或写操作。

功能：

- 登录 / 退出 / 修改密码；
- 首次登录风险揭示确认；
- 管理员创建/禁用/删除用户；
- 所有写操作与登录事件写入审计日志；
- WebUI 登录弹窗、用户角色显示、退出按钮。

```bash
uv run alphaagents users list
uv run alphaagents users create alice --role analyst
uv run alphaagents users passwd alice
uv run alphaagents users delete alice
uv run alphaagents audit --limit 50
```

API：

- `GET /api/auth/config`
- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/me`
- `POST /api/auth/change-password`
- `POST /api/auth/risk-ack`
- `GET/POST /api/users`（管理员）
- `PATCH/DELETE /api/users/{username}`（管理员）
- `GET /api/audit`（管理员）

> 多用户模式下已做基础数据归属：报告、自选股、组合账户、模拟盘账户按
> `owner` 隔离；管理员可查看全部，普通用户只能访问自己的数据。旧数据
> `owner=''` 默认仅管理员可见；定时任务仍以共享工作区运行。

### 鉴权与可观测性端点

设置 `WEBUI_API_KEY` 后，所有 `/api` 路由（`/healthz`、`/readyz`、`/metrics`
除外）都需要 `X-API-Key` 或 `Authorization: Bearer`：

```bash
WEBUI_API_KEY=your-secret-key
```

```bash
curl -H "X-API-Key: your-secret-key" http://localhost:8000/api/portfolio
```

- `GET /healthz` 存活探针
- `GET /readyz` 就绪探针（检查数据库与调度器线程）
- `GET /metrics` Prometheus 文本格式指标

### 独立调度（不依赖 WebUI）

调度器可以从 WebUI 中独立出来，用 cron / systemd timer 运行：

```bash
# 单次执行（cron 推荐）
uv run python -m tradingagents.scheduler --once

# 常驻
uv run python -m tradingagents.scheduler --loop
```

### 数据源

无需配置即可运行，内置优先级：

```
AkShare  →  BaoStock
```

- `data_vendors` 中列出的供应商就是**完整的降级链**，未列出的不会被调用
  （此前会把 yfinance / Alpha Vantage 无条件追加到链尾，A 股取数失败后
  仍会去打 Yahoo，产生大量 429 噪音）
- 默认 `A_SHARE_ONLY=1`：非 A 股标的在取数前即被拒绝
- 需要多市场能力时设置 `A_SHARE_ONLY=0`，并在 `data_vendors` 中显式加入
  `yfinance` 或 `alpha_vantage`

`akshare_adapter.py` 实现了限流、退避、超时与列名归一，`baostock_adapter.py`
提供历史数据兜底。

#### 本地行情仓库

日线行情默认缓存到 `data/market.db`（可通过 `TA_MARKET_DB` 修改）：

- 回测 / 评估 / K 线 / 模拟盘统一走 `services/market_data.py`，**本地优先**；
- 只对缺失的首尾区间请求数据源，周末/节假日自动跳过；
- 在线失败但本地有数据时降级返回本地，并附带 warning；
- 设置 `TA_MARKET_CACHE=0` 可恢复"每次都联网"的旧行为。

```bash
# 显式同步（默认最近三年）
uv run alphaagents sync 600519
uv run alphaagents sync 600519 --start 2015-01-01

# 检查本地数据质量
uv run alphaagents data-quality 600519
```

API：

- `GET /api/data/status`：仓库概览（标的数 / K 线数 / 覆盖范围）
- `GET /api/data/symbols`：已缓存标的列表
- `POST /api/data/sync`：同步指定标的
- `GET /api/data/quality?ticker=600519`：数据质量检查

### 启动行为

**打开 WebUI 不会自动执行任何分析。** 分析只能由用户点击“开始分析”或
显式调用 `POST /api/analyze` 触发。定时任务需要显式开启：

```bash
SCHEDULE_ENABLED=1
```

---

## 📁 项目结构

```
AlphaAgents-CN/
├── tradingagents/              # 核心引擎
│   ├── agents/
│   │   ├── analysts/            # 分析师（并行作业）
│   │   ├── researchers/         # 多头 / 空头研究员
│   │   ├── managers/            # 研究总监 / 组合经理
│   │   ├── risk_mgmt/           # 激进 / 中性 / 保守
│   │   └── trader/              # 交易员
│   ├── graph/                   # LangGraph 状态图
│   ├── dataflows/
│   │   ├── akshare_adapter.py   # A 股数据适配（行情/财务/新闻/资金流）
│   │   ├── baostock_adapter.py  # 历史数据兜底
│   │   ├── ashare_rules.py      # 交易所/涨跌停/ST/北交所规则
│   │   ├── trade_calendar.py    # A 股交易日历
│   │   ├── cache_provider.py    # 离线缓存兜底
│   │   └── interface.py         # 供应商路由
│   ├── services/                # 无界面分析服务与回测
│   ├── storage/                 # SQLite 报告中心 / 自选股
│   ├── scheduler.py             # 每日定时分析
│   └── llm_clients/             # 多厂商 LLM 客户端
├── cli/                         # 命令行界面
├── webui/                       # Web 工作台
├── docs/                        # 中文架构文档
└── tests/                       # 测试
```

---

## 📚 文档

| 文档 | 说明 |
|---|---|
| [Docker 打包与部署](docs/DOCKER.md) | 镜像构建、多架构、推送 GHCR/ACR/TCR、服务器部署、备份与排障 |
| [功能与 API 说明](docs/FEATURES_AND_API.md) | 功能清单、配置、CLI、HTTP API、WebSocket、定时任务 |
| [代码流程讲解](docs/代码流程讲解.md) | 按执行顺序串联所有代码文件 |
| [图解指南](docs/图解指南.md) | Mermaid 可视化架构图 |
| [新手入门指南](docs/新手入门指南.md) | 面向零基础 Python 学习者 |

---

## 📄 致谢与许可

本项目基于 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
（Apache 2.0）二次开发，感谢原作者 Yijia-Xiao 及 TauricResearch 团队的出色工作。

- **许可证**：Apache License 2.0 —— 可自由用于商业用途
- **归属声明**：见 [NOTICE](NOTICE)
- 部分中文 prompt 模板参考了
  [KylinMountain/TradingAgents-AShare](https://github.com/KylinMountain/TradingAgents-AShare)
  （Apache 2.0 部分）

详细说明见 [NOTICE](NOTICE) 与 [LICENSE](LICENSE)。

---

## ⚠️ 免责声明

本项目仅供学习研究与技术演示使用，**不构成任何投资建议**。
证券市场有风险，投资需谨慎。分析结果基于公开数据与模型推理，
可能存在延迟、偏差或错误。据此操作，风险自负。

## 📈 数据来源

行情与财务数据来自 AkShare、BaoStock、yfinance 等公开接口，
版权归各数据源所有。本项目不对数据的准确性、完整性、及时性作任何担保。
