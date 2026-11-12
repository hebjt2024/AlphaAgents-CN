# AlphaAgents-CN 功能与 API 说明

> 整理时间：2026-11-12  
> 适用版本：0.3.0  
> 适用范围：WebUI、CLI、HTTP API、定时任务、通知、数据服务  
> 说明：本文档不包含任何真实 API Key；示例数据仅用于说明字段结构。

---

## 目录

1. [系统概览](#1-系统概览)
2. [功能总览](#2-功能总览)
3. [新增与修改功能清单](#3-新增与修改功能清单)
4. [快速开始](#4-快速开始)
5. [配置说明](#5-配置说明)
6. [WebUI 使用说明](#6-webui-使用说明)
7. [CLI 命令](#7-cli-命令)
8. [关键模块与目录](#8-关键模块与目录)
9. [HTTP API 约定](#9-http-api-约定)
10. [HTTP API 一览](#10-http-api-一览)
11. [核心 API 详细说明](#11-核心-api-详细说明)
12. [WebSocket 协议](#12-websocket-协议)
13. [策略库说明](#13-策略库说明)
14. [决策卡 / 市场复盘 / 每日简报结构](#14-决策卡--市场复盘--每日简报结构)
15. [定时任务与通知](#15-定时任务与通知)
16. [数据源与降级策略](#16-数据源与降级策略)
17. [测试与验证](#17-测试与验证)
18. [已知限制与注意事项](#18-已知限制与注意事项)
19. [维护记录](#19-维护记录)

---

## 1. 系统概览

AlphaAgents-CN 是一个面向 A 股的多智能体投研系统，基于 TradingAgents 架构扩展，覆盖：

- A 股数据适配与交易规则
- 多智能体分析流程
- 可编排的分析流水线
- 经典策略扫描、回测与结构化决策
- 自选股策略预警
- 市场复盘、行业/概念热度、资金流、板块历史
- 每日盘后简报与归档
- WebUI、CLI、HTTP API、定时任务和 Webhook 通知

默认分析流程：

```text
策略扫描
  → 市场 / 舆情 / 新闻 / 基本面分析师（默认并行）
  → 多头研究员 vs 空头研究员
  → 研究经理
  → 交易员
  → 激进 / 中性 / 保守风险辩论
  → 组合经理最终裁决
```

精简模式可以跳过辩论、交易员和风险阶段，但组合经理始终输出 `final_trade_decision`。

### 技术栈

| 层 | 技术 |
|---|---|
| 语言 | Python 3.10+ |
| Web 服务 | FastAPI + Uvicorn |
| 实时通信 | WebSocket |
| 多智能体图 | LangGraph |
| 前端 | 原生 HTML/CSS/JS + ECharts |
| 数据 | AkShare、BaoStock、本地缓存，可选 yfinance / Alpha Vantage |
| 存储 | SQLite |
| LLM | Anthropic / MiniMax / OpenAI / Google / xAI / OpenRouter / Ollama |

---

## 2. 功能总览

| 功能 | 说明 | 入口 |
|---|---|---|
| A 股数据适配 | 代码归一化、北交所、B 股排除、交易日历、停牌提示、涨跌停/ST 规则 | 数据层 / CLI / WebUI |
| 多智能体分析 | 分析师、多空辩论、交易员、风险辩论、组合经理 | WebUI / CLI |
| 并行分析 | 多个分析师并行执行，缩短首轮耗时；并发度可配置 | 默认开启 |
| 快速预览 | 策略规则初判，不调用 LLM；深度分析同时后台运行 | WebUI / API |
| 异步任务 API | `job_id`、状态、结果、取消、删除 | API / 自动化 |
| 股票搜索 | 代码/名称搜索与 WebUI 自动补全 | WebUI / API |
| 热门股 / 涨停池 | 连板数、封板资金、炸板次数、行业 | WebUI / API |
| 可编排流水线 | 精简 / 标准 / 深度 / 自定义 | WebUI |
| 策略库 | 15 个经典策略，默认核心 6 个 | WebUI / CLI / API |
| 策略族去重 | 15 个策略归并为 7 个独立假设族，族内去重后再计票 | 策略扫描 / 决策卡 |
| 策略扫描 | 单票或自选股批量扫描 | WebUI / CLI / API |
| 策略回测 | 统一引擎：涨跌停不可成交、T+1、停牌、整手、滑点、ATR 止损 | WebUI / API |
| 回测统计门槛 | 样本内/样本外切分、有效样本量、显著性检验、多重检验校正 | WebUI / API |
| 结构化决策卡 | 评分、置信度、止损/目标、风险收益比、仓位 | WebUI / 报告 |
| 决策分歧可见 | 规则初判与 LLM 最终决策的差异随结果返回 | API / 报告 |
| 组合管理 | 账户、持仓账本、成交流水、组合快照 | API / 存储 |
| 组合级风控 | 单票/行业/总仓位上限、单笔风险预算、亏损与回撤熔断 | API / 决策 |
| 模拟盘 | T+1 批次锁定、涨跌停不可成交、整手、现金冻结、决策卡转委托单 | 服务层 |
| 评估闭环 | 决策与前瞻收益对齐、命中率汇总、有效样本量口径 | 服务层 |
| 反思与记忆 | 分析结束写入角色记忆并落库，下次分析可检索 | 图编排 |
| 可观测性 | token / 成本 / 逐阶段耗时采集与落库 | API / 存储 |
| 访问鉴权 | 可选 API Key（`WEBUI_API_KEY`）、限流、请求 ID | API |
| 任务队列 | 有界 worker 队列、原子认领、取消、僵尸回收、优雅退出 | API / Scheduler |
| 自选股预警 | 只保留触发信号，支持手动/定时 Webhook 推送 | WebUI / API / Scheduler |
| 市场复盘 | 指数、情绪、行业/概念热度、资金流、涨停分布 | WebUI / API |
| 板块历史 | 同花顺行业 1/5/20 日涨跌幅和 60 日走势 | WebUI / API |
| 每日盘后简报 | 市场复盘 + 自选股预警 + 明日关注 | WebUI / API / Scheduler |
| 简报归档 | SQLite 按日期归档，报告中心查看/删除 | WebUI / API |
| 报告中心 | 分析报告和每日简报历史 | WebUI |
| 定时任务 | 每日分析、预警、复盘、简报 | Scheduler |
| 定时任务管理 | WebUI/API 添加、暂停、删除每只股票的定时分析 | WebUI / API |
| 通知 | 企业微信 / 飞书 / 通用 Webhook | Scheduler / WebUI |
| Tavily 搜索 | `search_web` / `search_stock_news` | 新闻/舆情分析师 |
| 主题与图表 | 浅色/深色主题、ECharts K 线、缩放、全屏 | WebUI |
| 离线前端资源 | ECharts / marked / DOMPurify 本地化，不依赖 CDN | WebUI |

---

## 3. 新增与修改功能清单

### 3.1 A 股适配与数据安全

- 股票代码归一化：
  - `600519`、`sh600519`、`600519.SH`、`600519.XSHG`
  - 北交所 `920xxx`
  - 非 A 股代码返回空字符串
- B 股排除、ST / *ST 风险提示、涨跌停规则提示
- 交易日历校验：
  - 非交易日提示最近交易日
  - 分析日期校验接口
- 停牌/缺失交易日提示
- 历史估值防未来函数：按报告可用日期过滤
- 数据源优先级：AkShare → BaoStock → 本地缓存
- `ALLOW_SYNTHETIC_DATA=0` 时 fail-closed，避免用模拟数据生成投资结论
- 资金流、北向资金、龙虎榜等 A 股特色数据工具

### 3.2 LLM 配置与鉴权

- `.env` 驱动默认 provider / model / backend URL
- 支持 `MINIMAX_API_KEY` 作为 Anthropic 兼容端点的密钥别名
- 分析前预检 API Key，减少运行到中途才报鉴权错误
- WebUI 设置页显示 provider 配置状态
- `/api/test-llm` 可测试模型端点连通性
- MiniMax 官方 Anthropic 兼容地址：

```text
https://api.minimax.cn/anthropic
```

> 注意：不要使用 `api.minimaxi.cn`，该域名存在 SSL 连接问题。

### 3.3 可编排多智能体图

- 分析师可多选：市场、舆情、新闻、基本面
- 可开关多空辩论、交易员、风险辩论
- 预设：精简 / 标准 / 深度
- 自定义模式可单独设置辩论轮次
- 策略扫描作为图的第一个独立节点
- 条件建边和状态字段补齐

### 3.4 策略库与回测

- 15 个策略，默认核心 6 个
- 单票策略扫描、自选股批量扫描
- 策略回测：
  - 5/10/20 日前瞻收益
  - 胜率、最好/最差收益
  - 多头跟随、看空避险净值
  - 最大回撤、夏普、交易数
  - 基准收益
- A 股成本：佣金、卖出印花税
- 次日开盘执行，避免未来函数
- 看空策略不做空，只做“避险”模拟

### 3.5 结构化决策卡

根据策略共识 + 最终决策 + ATR/支撑压力位生成：

- 综合参考分
- 置信度
- 策略共识
- 参考价
- 止损参考
- 目标参考
- 风险收益比
- 建议仓位
- 理由与免责声明

该卡会进入：

- WebUI 分析结果页
- `analysis_complete` WebSocket
- 报告中心
- 报告 Markdown 导出
- 定时任务结果

### 3.6 WebUI 改造

- 三个视图：工作台 / 报告中心 / 设置
- 浅色 / 深色主题，localStorage 持久化
- ECharts K 线：
  - 1M / 3M / 6M / 1Y / 3Y / 5Y / 全部
  - 滚轮缩放、拖动平移、区间滑块
  - MA5 / MA20
  - 全屏
- 策略选择、策略扫描、策略回测
- 自选股预警、市场复盘、板块历史、每日简报
- 报告中心新增每日简报归档
- 设置页支持 provider / model / Base URL 和连通性测试

### 3.7 Tavily 联网搜索

- 可选依赖；未配置时给出明确提示，不阻塞其他分析
- 工具：
  - `search_web`
  - `search_stock_news`
- 接入新闻/舆情分析链路

### 3.8 自选股策略预警

- 扫描自选股并只保留触发策略的标的
- 展示触发策略、方向、评分、理由
- 支持手动推送
- 支持定时推送
- 无触发信号时不推送，避免噪音
- Webhook：企业微信 / 飞书 / 通用 JSON Webhook

### 3.9 市场复盘与热度

- 主要指数
- 涨跌家数、平盘、涨停/跌停、真实涨跌停、活跃度
- 行业领涨/领跌
- 概念领涨/领跌
- 行业资金流：净流入/净流出
- 涨停行业分布
- 板块热度历史：同花顺行业 1/5/20 日涨跌幅和 60 日走势
- 规则化复盘结论
- Markdown 导出/复制

### 3.10 每日盘后简报与归档

- 合并市场复盘、自选股预警、明日关注
- 支持查看、复制、归档、推送
- 定时推送自动归档
- 同一天重复归档覆盖
- 报告中心可查看和删除

### 3.11 定时任务与通知

- 每日定时分析
- 每日自选股预警
- 每日市场复盘
- 每日盘后简报
- 企业微信 / 飞书 / 通用 Webhook
- 交易日判断，非交易日自动跳过
- `SCHEDULE_BRIEFING=1` 优先合并通知，避免重复推送

### 3.12 历史问题修复

| 问题 | 处理方式 |
|---|---|
| Python 依赖冲突（parsel / jmespath 等） | 统一使用 `uv` 和 `uv.lock` 管理依赖 |
| 首次运行缺少 `.env` 导致鉴权失败 | 分析前预检 Key，设置页显示配置状态 |
| MiniMax 端点 SSL 失败 | 统一使用 `https://api.minimax.cn/anthropic` |
| 意图解析回归 | 修复代码/名称识别，保留回退逻辑 |
| K 线不可交互 | 改为 ECharts，支持缩放、平移、全屏、区间切换 |
| 策略扫描不直观 | 增加策略选择、进度阶段、策略标签 |
| 策略结果未进入分析 | 增加独立 Strategy Scanner 图节点并注入分析师 |
| 数据源限流/断连 | 多源降级、重试、缓存、warnings 提示 |
| 通知无内容时噪音 | 预警默认无触发不推送；复盘/简报数据不可用时跳过 |
| 报告无法追溯 | SQLite 报告中心 + 每日简报归档 |

### 3.13 任务化、搜索与离线资源

- 新增 SQLite `jobs` 表，保存 `job_id`、状态、结果和错误
- `/api/analyze` 返回 `job_id`，支持 REST 轮询
- 新增任务列表、详情、结果、取消、删除接口
- 新增股票代码/名称搜索接口，WebUI 股票输入支持自动补全
- 新增涨停池热门股接口和 WebUI 表格
- ECharts / marked / DOMPurify 改为本地 `/static/vendor/` 资源，不再依赖 CDN
- 新增 `scheduled_jobs` 表和定时任务 CRUD API
- WebUI 设置页可添加、暂停、删除定时分析股票
- 定时任务与原有 `SCHEDULE_STOCKS` 兼容，统一受 `SCHEDULE_ENABLED=1` 控制
- 新增快速预览：策略扫描 + 规则初判，不调用 LLM；WebUI 在深度分析期间先展示
- 分析师默认并行执行，支持通过环境变量调整并发度和回退串行

---

## 4. 快速开始

### 4.1 安装

推荐使用 `uv`：

```bash
uv sync
```

或：

```bash
pip install -e .
```

### 4.2 配置 `.env`

```bash
cp .env.example .env
```

至少填写一个 LLM Key，例如 MiniMax：

```bash
ANTHROPIC_API_KEY=你的Key
# 或
MINIMAX_API_KEY=你的Key

TRADINGAGENTS_LLM_PROVIDER=anthropic
TRADINGAGENTS_DEEP_THINK_LLM=MiniMax-M3
TRADINGAGENTS_QUICK_THINK_LLM=MiniMax-M3.1-Flash
TRADINGAGENTS_BACKEND_URL=https://api.minimax.cn/anthropic
```

### 4.3 启动 WebUI

```bash
uv run python webui/run_webui.py --port 8000
```

访问：

```text
http://127.0.0.1:8000
```

常用参数：

```bash
uv run python webui/run_webui.py --host 0.0.0.0 --port 8000
uv run python webui/run_webui.py --reload
```

健康检查：

```bash
curl http://127.0.0.1:8000/healthz
```

### 4.4 使用 CLI

```bash
uv run alphaagents analyze
uv run alphaagents history
uv run alphaagents strategies 600519
```

### 4.5 使用 Docker

```bash
docker compose up --build
```

默认端口 `8000`，可以通过 `PORT` 覆盖：

```bash
PORT=8080 docker compose up --build
```

数据持久化目录：

- `./data`
- `./eval_results`
- `./logs`

---

## 5. 配置说明

### 5.1 LLM 提供商

| 环境变量 | 说明 |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic / MiniMax Anthropic 兼容端点 |
| `MINIMAX_API_KEY` | MiniMax Key，可作为 anthropic provider 的别名 |
| `OPENAI_API_KEY` | OpenAI |
| `GOOGLE_API_KEY` / `GOOGLE_GENAI_API_KEY` | Google Gemini |
| `XAI_API_KEY` | xAI |
| `OPENROUTER_API_KEY` | OpenRouter |
| `OLLAMA` | 本地 Ollama，无需 Key |

默认模型配置：

| 环境变量 | 默认值 |
|---|---|
| `TRADINGAGENTS_LLM_PROVIDER` | `anthropic` |
| `TRADINGAGENTS_DEEP_THINK_LLM` | `MiniMax-M3` |
| `TRADINGAGENTS_QUICK_THINK_LLM` | `MiniMax-M3.1-Flash` |
| `TRADINGAGENTS_BACKEND_URL` | `https://api.minimax.cn/anthropic` |
| `TRADINGAGENTS_MAX_DEBATE_ROUNDS` | `1` |
| `TRADINGAGENTS_MAX_RISK_DISCUSS_ROUNDS` | `1` |

WebUI 支持的 provider 默认模型：

| Provider | Deep | Quick |
|---|---|---|
| anthropic | `MiniMax-M3` | `MiniMax-M3.1-Flash` |
| openai | `gpt-4.1` | `gpt-4o-mini` |
| google | `gemini-2.5-pro` | `gemini-2.5-flash` |
| xai | `grok-4-0709` | `grok-4-1-fast-non-reasoning` |
| openrouter | `z-ai/glm-4.5-air:free` | `z-ai/glm-4.5-air:free` |
| ollama | `qwen3:latest` | `qwen3:latest` |

### 5.2 数据源

| 环境变量 | 说明 |
|---|---|
| `ALPHA_VANTAGE_API_KEY` | Alpha Vantage，可选 |
| `TAVILY_API_KEY` / `TAVILY_API_KEYS` | Tavily 联网搜索，可选 |
| `AKSHARE_TIMEOUT` | AkShare 超时秒数，默认 30 |
| `AKSHARE_MAX_RETRIES` | 默认 3 |
| `AKSHARE_BASE_DELAY` | 默认 2.0 |
| `AKSHARE_REQUEST_INTERVAL` | 默认 0.5 |
| `TQDM_DISABLE` | 设为 1 可关闭部分 AkShare 进度条 |
| `YFINANCE_PROXY` | 可选 |
| `HTTPS_PROXY` | 可选 |

### 5.3 数据安全与存储

| 环境变量 | 说明 |
|---|---|
| `ALLOW_SYNTHETIC_DATA` | 默认 `0`，数据源全部失败时 fail-closed |
| `TA_DB_PATH` | SQLite 路径，默认 `<cwd>/data/tradingagents.db` |
| `TRADINGAGENTS_RESULTS_DIR` | 结果目录，默认 `./results` |

### 5.4 通知

| 环境变量 | 说明 |
|---|---|
| `WECOM_WEBHOOK_URL` | 企业微信机器人 |
| `FEISHU_WEBHOOK_URL` | 飞书机器人 |
| `GENERIC_WEBHOOK_URL` | 通用 JSON Webhook |

未配置任何渠道时不会报错，只返回 `skipped`。

### 5.5 性能优化

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `TRADINGAGENTS_PARALLEL_ANALYSTS` | `1` | 是否并行执行多个分析师 |
| `TRADINGAGENTS_PARALLEL_ANALYSTS_WORKERS` | `4` | 并行分析师的最大并发度 |

说明：

- 并行分析师各自使用独立的 messages 副本，报告在节点结束时合并
- 默认分析师使用 quick model，只有研究经理/组合经理使用 deep model
- 如果模型或数据源限流，把 workers 降到 `2`，或设置 `PARALLEL_ANALYSTS=0` 回退串行
- 快速查看可使用“精简”流水线；长分析建议通过任务 API 启动后轮询
- WebUI 启动分析时会同时请求 `/api/preview`，先展示规则化预览，再等待深度结果

### 5.6 定时任务

| 环境变量 | 说明 |
|---|---|
| `SCHEDULE_ENABLED` | `1` 启用定时任务 |
| `SCHEDULE_TIME` | 每日执行时间，默认 `18:00` |
| `SCHEDULE_STOCKS` | 定时分析股票，逗号分隔 |
| `SCHEDULE_ALERTS` | `1` 推送自选股策略预警 |
| `SCHEDULE_REVIEW` | `1` 推送市场复盘 |
| `SCHEDULE_BRIEFING` | `1` 推送合并后的每日简报，优先于上面两项 |

WebUI 设置页还可以管理 `scheduled_jobs` 中的逐只股票定时分析；这些任务与
`SCHEDULE_STOCKS` 并存，统一受 `SCHEDULE_ENABLED=1` 控制。

示例：

```bash
SCHEDULE_ENABLED=1
SCHEDULE_TIME=18:00
SCHEDULE_STOCKS=600519,000001
SCHEDULE_ALERTS=1
SCHEDULE_REVIEW=1
# 或使用合并简报
# SCHEDULE_BRIEFING=1
```

---

## 6. WebUI 使用说明

### 6.1 工作台

主要区域：

- 股票代码、分析日期、分析周期
- 智能意图解析
- 流水线预设：精简 / 标准 / 深度 / 自定义
- 分析师选择
- 策略选择
- 启动 / 停止分析
- 实时进度、日志、结果

### 6.2 快捷工具

| 工具 | 说明 |
|---|---|
| 回测 MA5/20 | 均线交叉回测 |
| 主力资金流向 | 个股资金流柱状图 |
| 策略扫描（当前选择） | 单票策略扫描 |
| 策略回测（当前选择） | 策略信号有效性回测 |
| 市场复盘 | 指数、情绪、行业/概念、资金流、板块历史 |
| 每日盘后简报 | 复盘 + 预警 + 明日关注 |

### 6.3 自选股

- 添加 / 删除自选股
- 点击自选股快速填入分析代码
- 扫描自选股策略共识
- 触发预警
- 推送预警

### 6.4 研判进度

- 阶段进度：策略信号、分析团队、多空辩论、交易执行、风险辩论、最终裁决
- 当前节点高亮
- 本次启用的策略标签
- 策略扫描状态：待扫描 / 扫描中 / 已扫描

### 6.5 分析结果

- 最终决策徽章
- 结构化决策卡
- 最终决策全文
- 市场 / 舆情 / 新闻 / 基本面 / 研究经理 / 交易员 / 风险报告
- 导出 Markdown

### 6.6 报告中心

- 历史分析报告列表
- 搜索股票代码 / 决策
- 查看完整报告
- 删除报告
- 每日简报归档列表
- 查看 / 删除每日简报

### 6.7 设置

- LLM Provider
- Deep / Quick 模型
- Base URL
- 辩论轮次
- 数据源配置状态
- Tavily 配置状态
- 模型连通性测试
- 定时任务状态
- 定时分析任务：添加、暂停、启用、删除

---

## 7. CLI 命令

入口：

```bash
alphaagents <command>
# 或
tradingagents <command>
```

| 命令 | 说明 | 示例 |
|---|---|---|
| `analyze` | 交互式分析 | `alphaagents analyze` |
| `history [ticker]` | 查看历史报告 | `alphaagents history 600519` |
| `view <ticker> [date]` | 查看指定报告 | `alphaagents view 600519 2026-11-11` |
| `backtest <ticker>` | 均线/买入持有回测 | `alphaagents backtest 600519 --start 2023-01-01 --end 2024-12-31` |
| `strategies <ticker>` | 策略扫描 | `alphaagents strategies 600519 --strategies bull_trend,volume_breakout` |
| `strategies <ticker> --all` | 扫描全部 15 个策略 | `alphaagents strategies 600519 --all` |
| `strategies <ticker> --json` | JSON 输出 | `alphaagents strategies 600519 --json` |
| `watch add <ticker>` | 添加自选股 | `alphaagents watch add 600519 --name 贵州茅台` |
| `watch list` | 查看自选股 | `alphaagents watch list` |
| `watch remove <ticker>` | 删除自选股 | `alphaagents watch remove 600519` |
| `jobs list` | 查看分析任务记录 | `alphaagents jobs list -n 20` |
| `jobs clean` | 清理终态任务记录 | `alphaagents jobs clean --dry-run` |
| `jobs purge` | 按条件清理任务记录 | `alphaagents jobs purge --before 2026-11-01` |

任务记录清理的安全约定：

- 默认只删除**终态**任务（`completed` / `failed` / `cancelled`），
  不会误删 `queued` / `running`；
- `--dry-run` 只预览命中数量与状态/来源分布，不删除；
- 支持 `--before YYYY-MM-DD`、`--company <代码>`、`--status a,b` 过滤；
- `--yes` 跳过确认（便于脚本化）。

回测参数：

```bash
alphaagents backtest 600519 \
  --start 2023-01-01 \
  --end 2024-12-31 \
  --strategy ma_cross \
  --fast 5 \
  --slow 20 \
  --cash 100000
```

---

## 8. 关键模块与目录

```text
tradingagents/
├── agents/                  # 分析师、研究员、交易员、风险辩论、组合经理
│   └── strategy_scanner.py  # 策略扫描图节点
├── dataflows/
│   ├── akshare_adapter.py   # A 股数据适配
│   ├── baostock_adapter.py  # 备用数据源
│   ├── ashare_rules.py      # A 股代码/涨跌停/ST 规则
│   ├── trade_calendar.py    # 交易日历
│   ├── tavily_search.py     # Tavily 搜索
│   └── cache_provider.py    # 本地缓存
├── graph/
│   ├── setup.py             # 图构建与条件边
│   ├── propagation.py       # 状态初始化
│   ├── pipeline_presets.py  # 流水线预设
│   └── intent_parser.py     # 自然语言意图解析
├── strategies/
│   ├── classic.py           # 15 个策略 + 元数据
│   ├── scanner.py           # 策略扫描/自选股扫描
│   └── base.py              # 信号结构 + 指标工具
├── services/
│   ├── backtest.py          # MA/买入持有回测
│   ├── strategy_backtest.py # 策略有效性回测
│   ├── decision_plan.py     # 结构化决策卡
│   ├── strategy_alerts.py   # 自选股策略预警
│   ├── market_review.py     # 市场复盘 + 行业资金流
│   ├── sector_history.py    # 板块热度历史
│   ├── daily_briefing.py    # 每日盘后简报
│   ├── market_search.py     # 股票搜索 + 涨停池热门股
│   └── runner.py            # 分析服务封装
├── storage/
│   ├── report_store.py      # 分析报告 SQLite
│   ├── briefing_store.py    # 每日简报 SQLite
│   ├── job_store.py         # 分析任务 SQLite
│   ├── scheduled_store.py   # 定时分析任务 SQLite
│   └── watchlist_store.py   # 自选股 SQLite
├── notify.py                # 企业微信/飞书/通用 Webhook
├── scheduler.py             # 定时任务
├── default_config.py        # 环境变量默认配置
└── llm_clients/             # LLM 客户端、鉴权、工厂

cli/
└── main.py                  # Typer CLI

webui/
├── backend/server.py        # FastAPI + WebSocket + 任务 API
└── frontend/
    ├── index.html           # 单页 WebUI
    └── vendor/              # 本地 ECharts / marked / DOMPurify

tests/                       # 单元/接口测试
```

---

## 9. HTTP API 约定

### 9.1 Base URL

```text
http://127.0.0.1:8000
```

静态资源：

```text
/static
```

### 9.2 通用响应

大多数接口返回：

```json
{
  "success": true
}
```

失败：

```json
{
  "success": false,
  "message": "错误原因"
}
```

框架级错误（校验失败、404、限流、未授权等）统一使用同一套信封，
并附带机器可读的 `code` 与 `request_id`：

```json
{
  "success": false,
  "code": "validation_error",
  "message": "错误原因",
  "details": null,
  "request_id": "6f1c0a2e9b7d4a1e9c3f4b2d1e0a9c8b"
}
```

`request_id` 同时通过响应头 `X-Request-ID` 返回，便于把前端提示与服务端
日志对应起来。错误码：`bad_request`、`unauthorized`、`not_found`、
`conflict`、`validation_error`、`rate_limited`、`internal_error`、
`service_unavailable`、`http_error`。

个别接口直接返回业务对象，例如：

- `GET /api/status`
- `GET /api/scheduler/status`
- `GET /healthz`

### 9.3 约定

- 日期格式：`YYYY-MM-DD`
- 策略列表：逗号分隔，例如 `bull_trend,volume_breakout`
- 股票代码支持常见 A 股写法，服务内部会归一化
- 阻塞型数据请求通过线程池执行，避免阻塞 FastAPI 事件循环
- 鉴权默认关闭（便于本地使用）；配置 `WEBUI_API_KEY` 后开启，
  详见 §10.7
- 分析任务走有界队列：超出 `WEBUI_MAX_PENDING_ANALYSES` 时返回 409

---

## 10. HTTP API 一览

### 10.1 系统与配置

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | WebUI 页面 |
| GET | `/static/*` | 前端静态资源 |
| GET | `/healthz` | 健康检查 |
| GET | `/api/status` | 当前分析状态 |
| POST | `/api/analyze` | 启动分析，返回 `job_id` |
| POST | `/api/stop` | 请求停止当前分析 |
| GET | `/api/jobs` | 任务列表 |
| GET | `/api/jobs/{job_id}` | 任务状态 |
| GET | `/api/jobs/{job_id}/result` | 任务结果 |
| POST | `/api/jobs/{job_id}/cancel` | 取消任务 |
| DELETE | `/api/jobs/{job_id}` | 删除任务 |
| POST | `/api/intent` | 解析自然语言意图 |
| POST | `/api/test-llm` | 测试 LLM 端点 |
| GET | `/api/config/defaults` | 获取 WebUI 默认模型/地址 |
| GET | `/api/pipeline/presets` | 获取流水线预设和分析师元数据 |
| GET | `/api/settings/status` | 获取 Key / 数据源配置状态 |
| GET | `/api/scheduler/status` | 获取定时任务基础状态 |
| GET | `/api/scheduled` | 定时分析任务列表 |
| POST | `/api/scheduled` | 添加定时分析任务 |
| PATCH | `/api/scheduled/{id}` | 更新/暂停定时任务 |
| DELETE | `/api/scheduled/{id}` | 删除定时任务 |
| GET | `/api/trade-calendar/check` | 校验分析日期 |
| WS | `/ws` | 实时进度和结果 |

### 10.2 分析报告与简报

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/reports` | 报告列表 |
| GET | `/api/reports/{id}` | 报告详情 |
| DELETE | `/api/reports/{id}` | 删除报告 |
| GET | `/api/daily-briefing` | 生成每日简报 |
| POST | `/api/daily-briefing/notify` | 生成、归档并推送简报 |
| POST | `/api/daily-briefing/archive` | 生成并归档简报 |
| GET | `/api/briefings` | 简报归档列表 |
| GET | `/api/briefings/{id}` | 简报详情 |
| DELETE | `/api/briefings/{id}` | 删除简报 |

### 10.3 自选股

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/watchlist` | 自选股列表 |
| POST | `/api/watchlist` | 添加自选股 |
| DELETE | `/api/watchlist/{ticker}` | 删除自选股 |

### 10.4 行情与回测

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/market/kline` | K 线数据 |
| POST | `/api/backtest` | MA / 买入持有回测（含 A 股执行约束） |
| GET | `/api/market/capital-flow` | 个股主力资金流 |
| GET | `/api/market/northbound` | 北向资金 |
| GET | `/api/market/review` | 市场复盘 |
| POST | `/api/market/review/notify` | 推送市场复盘 |
| GET | `/api/market/sectors` | 同花顺行业列表 |
| GET | `/api/market/sector-history` | 板块热度历史 |
| GET | `/api/market/stock-search` | 股票代码/名称搜索 |
| GET | `/api/market/hot-stocks` | 涨停池热门股 |

### 10.5 策略

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/preview` | 规则化快速预览（不调用 LLM） |
| GET | `/api/strategies/meta` | 策略元数据与默认组合 |
| GET | `/api/strategies` | 单票策略扫描 |
| GET | `/api/strategies/watchlist` | 自选股策略扫描 |
| GET | `/api/strategy-backtest` | 策略回测 |
| GET | `/api/strategy-alerts` | 自选股触发预警 |
| POST | `/api/strategy-alerts/notify` | 生成并推送预警 |

### 10.6 组合、运行指标与系统

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/portfolio` | 账户持仓、组合权重与风险体检 |
| POST | `/api/portfolio/evaluate` | 把决策卡放到组合语境下重新评估 |
| GET | `/api/runs` | 最近运行的 token / 成本 / 耗时指标 |
| GET | `/api/runs/aggregate` | 运行指标聚合（按模型与来源拆分） |
| GET | `/healthz` | 存活探针 |
| GET | `/readyz` | 就绪探针（检查数据库与调度器） |
| GET | `/metrics` | Prometheus 文本格式指标 |

### 10.7 鉴权与限流

配置 `WEBUI_API_KEY` 后，除 `/healthz`、`/readyz`、`/metrics` 与静态资源
外的所有 `/api` 路由都需要携带访问密钥：

```bash
curl -H "X-API-Key: your-key" http://localhost:8000/api/portfolio
# 或
curl -H "Authorization: Bearer your-key" http://localhost:8000/api/portfolio
```

WebSocket 无法自定义请求头，通过查询参数传递：`/ws?api_key=your-key`。

限流默认按来源 IP 计数（`WEBUI_RATE_LIMIT`，默认 120 次/分钟），
超出返回 429 与 `Retry-After`。

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `WEBUI_API_KEY` | 空（不鉴权） | 访问密钥 |
| `WEBUI_RATE_LIMIT` | 120 | 每分钟每 IP 请求上限 |
| `WEBUI_RATE_LIMIT_ENABLED` | 1 | 是否启用限流 |
| `WEBUI_ALLOWED_ORIGINS` | 空（同源） | 允许的跨域来源，逗号分隔 |
| `WEBUI_ANALYSIS_WORKERS` | 1 | 分析任务并发数 |
| `WEBUI_MAX_PENDING_ANALYSES` | 3 | 运行 + 排队的分析任务上限 |

---

## 11. 核心 API 详细说明

### 11.1 POST `/api/analyze`

启动一次分析。分析是异步的，结果通过 WebSocket 推送。

请求体：

```json
{
  "company": "600519",
  "date": "2026-11-11",
  "horizon": "medium",
  "llm_provider": "anthropic",
  "deep_think_llm": "MiniMax-M3",
  "quick_think_llm": "MiniMax-M3.1-Flash",
  "backend_url": "https://api.minimax.cn/anthropic",
  "pipeline_preset": "standard",
  "analysts": ["market", "social", "news", "fundamentals"],
  "enable_debate": true,
  "enable_trader": true,
  "enable_risk": true,
  "strategies": [
    "bull_trend",
    "ma_golden_cross",
    "volume_breakout",
    "shrink_pullback",
    "bottom_volume",
    "box_oscillation"
  ]
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `company` | string | 股票代码或名称 |
| `date` | string | 分析日期 `YYYY-MM-DD` |
| `horizon` | string | 分析周期 |
| `llm_provider` | string | LLM 提供商 |
| `deep_think_llm` | string | 深度思考模型 |
| `quick_think_llm` | string | 快速模型 |
| `backend_url` | string/null | 自定义 Base URL |
| `pipeline_preset` | string | `lite` / `standard` / `deep` / 自定义 |
| `analysts` | string[] | `market` / `social` / `news` / `fundamentals` |
| `enable_debate` | bool | 是否启用多空辩论 |
| `enable_trader` | bool | 是否启用交易员 |
| `enable_risk` | bool | 是否启用风险辩论 |
| `strategies` | string[] | 本次启用的策略 key |

响应：

```json
{
  "success": true,
  "message": "分析任务已启动: 600519 (2026-11-11)"
}
```

失败示例：

```json
{
  "success": false,
  "message": "运行中已有分析任务，请等待完成后再试"
}
```

### 11.2 GET `/api/status`

响应：

```json
{
  "running": false,
  "company": "",
  "date": "",
  "start_time": null,
  "job_id": ""
}
```

### 11.3 POST `/api/stop`

请求停止当前分析。停止是协作式的，会在当前节点完成后退出生效。

```json
{
  "success": true,
  "message": "已发送停止信号，将在当前节点完成后终止"
}
```

### 11.3.1 任务 API

`POST /api/analyze` 成功后会返回 `job_id`：

```json
{
  "success": true,
  "message": "分析任务已启动: 600519 (2026-11-11)",
  "job_id": "a1b2c3d4e5f6"
}
```

查询任务列表：

```http
GET /api/jobs?limit=20&offset=0
```

```json
{
  "success": true,
  "items": [
    {
      "job_id": "a1b2c3d4e5f6",
      "company": "600519",
      "trade_date": "2026-11-11",
      "status": "completed",
      "created_at": "2026-11-11T19:00:00",
      "started_at": "2026-11-11T19:00:01",
      "finished_at": "2026-11-11T19:02:30",
      "error": "",
      "meta": {}
    }
  ]
}
```

查询状态：

```http
GET /api/jobs/a1b2c3d4e5f6
```

查询结果：

```http
GET /api/jobs/a1b2c3d4e5f6/result
```

任务完成时：

```json
{
  "success": true,
  "job_id": "a1b2c3d4e5f6",
  "status": "completed",
  "result": {
    "decision": "BUY",
    "reports": {},
    "decision_plan": {},
    "report_id": 12
  }
}
```

任务运行中：

```json
{
  "success": false,
  "job_id": "a1b2c3d4e5f6",
  "status": "running",
  "message": "任务尚未完成"
}
```

取消：

```http
POST /api/jobs/a1b2c3d4e5f6/cancel
```

删除：

```http
DELETE /api/jobs/a1b2c3d4e5f6
```

状态值：`queued` / `running` / `completed` / `failed` / `cancelled`。

当前仍保持单分析并发控制；任务排队到获得 `ANALYSIS_LOCK` 后才会进入 `running`。

### 11.4 POST `/api/intent`

请求：

```json
{
  "query": "帮我看看贵州茅台未来一个月走势"
}
```

响应：

```json
{
  "success": true,
  "intent": {
    "query": "帮我看看贵州茅台未来一个月走势",
    "ticker": "600519",
    "horizons": ["short", "medium"],
    "focus_areas": ["technical", "fundamental", "news"],
    "raw_query": "帮我看看贵州茅台未来一个月走势"
  }
}
```

### 11.5 POST `/api/test-llm`

请求：

```json
{
  "llm_provider": "anthropic",
  "model": "MiniMax-M3.1-Flash",
  "backend_url": "https://api.minimax.cn/anthropic"
}
```

响应：

```json
{
  "success": true,
  "backend_url": "https://api.minimax.cn/anthropic",
  "model": "MiniMax-M3.1-Flash",
  "message": "连接成功：OK"
}
```

### 11.6 GET `/api/config/defaults`

响应字段：

- `llm_provider`
- `deep_think_llm`
- `quick_think_llm`
- `backend_url`
- `max_debate_rounds`
- `max_risk_discuss_rounds`
- `provider_base_urls`
- `provider_models`
- `parallel_analysts`
- `parallel_analysts_workers`

### 11.7 GET `/api/pipeline/presets`

响应：

```json
{
  "success": true,
  "presets": {
    "lite": {
      "label": "精简",
      "description": "两个分析师 + 组合经理，速度快、成本低",
      "analysts": ["market", "fundamentals"],
      "enable_debate": false,
      "enable_trader": false,
      "enable_risk": false,
      "max_debate_rounds": 1,
      "max_risk_discuss_rounds": 1
    }
  },
  "analysts": [
    {"key": "market", "label": "市场分析师", "description": "行情、技术指标、资金流"},
    {"key": "social", "label": "舆情分析师", "description": "社交媒体、新闻情绪"},
    {"key": "news", "label": "新闻分析师", "description": "新闻、公告、事件"},
    {"key": "fundamentals", "label": "基本面分析师", "description": "财报、估值、盈利能力"}
  ]
}
```

### 11.8 GET `/api/settings/status`

响应：

```json
{
  "success": true,
  "providers": [
    {"provider": "anthropic", "configured": true},
    {"provider": "openai", "configured": false},
    {"provider": "google", "configured": false},
    {"provider": "xai", "configured": false},
    {"provider": "openrouter", "configured": false},
    {"provider": "ollama", "configured": true}
  ],
  "minimax_configured": true,
  "tavily_configured": false,
  "default_provider": "anthropic"
}
```

### 11.9 GET `/api/scheduler/status`

响应：

```json
{
  "enabled": "1",
  "time": "18:00",
  "stocks": "600519,000001",
  "scheduled_jobs": 2
}
```

> 该接口只返回基础字段；`SCHEDULE_ALERTS` / `SCHEDULE_REVIEW` / `SCHEDULE_BRIEFING` 请查看环境变量或日志。

### 11.9.1 定时分析任务 API

添加：

```http
POST /api/scheduled
Content-Type: application/json

{
  "ticker": "600519",
  "name": "贵州茅台",
  "schedule_time": "18:00"
}
```

列表：

```http
GET /api/scheduled
```

```json
{
  "success": true,
  "items": [
    {
      "id": 1,
      "ticker": "600519",
      "name": "贵州茅台",
      "schedule_time": "18:00",
      "enabled": true,
      "last_run_date": "2026-11-11",
      "created_at": "2026-11-11T19:00:00",
      "updated_at": "2026-11-11T19:00:00"
    }
  ]
}
```

暂停/更新：

```http
PATCH /api/scheduled/1
Content-Type: application/json

{"enabled": false}
```

删除：

```http
DELETE /api/scheduled/1
```

说明：

- 需要 `SCHEDULE_ENABLED=1` 才会执行
- 每个交易日到 `schedule_time` 后执行一次
- 与 `SCHEDULE_STOCKS` 环境变量兼容
- 同股票 + 同时间重复添加会覆盖名称并重新启用

### 11.10 GET `/api/trade-calendar/check`

参数：

| 参数 | 说明 |
|---|---|
| `company` | 股票代码 |
| `date` | 分析日期 |

响应：

```json
{
  "success": true,
  "is_trading_day": true,
  "is_cn_symbol": true,
  "message": "2026-11-11 是交易日",
  "suggested_date": "2026-11-11"
}
```

### 11.11 GET `/api/market/kline`

参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `ticker` | 必填 | 股票代码 |
| `range` | `6m` | `1m` / `3m` / `6m` / `1y` / `3y` / `5y` / `all` |
| `limit` | 无 | 最多返回条数，20-6000 |
| `end_date` | 无 | 历史截止日期 |

响应：

```json
{
  "success": true,
  "ticker": "600519",
  "range": "1y",
  "bars": 242,
  "items": [
    {
      "date": "2026-11-11",
      "open": 1400.0,
      "high": 1420.0,
      "low": 1390.0,
      "close": 1402.3,
      "volume": 12345678.0
    }
  ]
}
```

### 11.12 POST `/api/backtest`

请求：

```json
{
  "ticker": "600519",
  "start_date": "2024-01-01",
  "end_date": "2024-12-31",
  "strategy": "ma_cross",
  "fast": 5,
  "slow": 20,
  "initial_cash": 100000.0,
  "commission_rate": 0.0003,
  "stamp_duty_rate": 0.0005,
  "slippage_rate": 0.0
}
```

响应：

```json
{
  "success": true,
  "result": {
    "ticker": "600519",
    "start_date": "2024-01-01",
    "end_date": "2024-12-31",
    "strategy": "ma_cross",
    "final_equity": 108500.0,
    "total_return": 0.085,
    "annualized_return": 0.085,
    "max_drawdown": -0.12,
    "sharpe": 0.8,
    "trade_count": 12,
    "win_rate": 0.5,
    "bars": 242,
    "cost_rate": 0.0012,
    "equity_curve": [
      {"date": "2024-01-02", "equity": 100000.0}
    ]
  }
}
```

### 11.13 GET `/api/market/capital-flow`

参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `ticker` | 必填 | 股票代码 |
| `days` | 60 | 5-120 |

响应：

```json
{
  "success": true,
  "ticker": "600519",
  "items": [
    {
      "date": "2026-11-11",
      "main_net": 12345678.0,
      "main_pct": 1.23,
      "close": 1402.3,
      "change_pct": -2.55
    }
  ]
}
```

### 11.14 GET `/api/market/northbound`

参数：`days`，默认 10，范围 1-60。

响应：

```json
{
  "success": true,
  "items": [
    {"date": "2026-11-11", "net": 123456789.0}
  ]
}
```

### 11.15 GET `/api/market/review`

参数：`date` 可选，仅用于提示；数据始终为实时快照。

响应核心字段：

```json
{
  "success": true,
  "as_of": "2026-11-11",
  "requested_date": "",
  "indices": [
    {
      "code": "sh000001",
      "name": "上证指数",
      "close": 3680.64,
      "change_pct": 0.46,
      "turnover_yi": 7361.5
    }
  ],
  "breadth": {
    "up": 3381,
    "down": 1647,
    "flat": 191,
    "up_ratio": 0.6478,
    "limit_up": 84,
    "real_limit_up": 75,
    "limit_down": 3,
    "real_limit_down": 2,
    "suspended": 6,
    "activity": "64.71%",
    "stat_time": "2026-11-11 15:00:00"
  },
  "sectors": {
    "total": 84,
    "top": [
      {
        "name": "计算机、通信和其他电子设备制造业",
        "change_pct": 6.92,
        "turnover_yi": 390.8,
        "stock_count": 100,
        "leader": "宇特光电",
        "leader_change_pct": 29.99
      }
    ],
    "bottom": [],
    "concept": {
      "total": 172,
      "top": [
        {
          "name": "钙钛矿",
          "change_pct": 8.48,
          "turnover_yi": 206.8,
          "stock_count": 38,
          "leader": "杰普特",
          "leader_change_pct": 14.91
        }
      ],
      "bottom": []
    }
  },
  "fund_flow": {
    "top": [
      {
        "name": "半导体",
        "change_pct": 3.09,
        "net_yi": 149.13,
        "inflow_yi": 861.75,
        "outflow_yi": 712.62,
        "stock_count": 190,
        "leader": "利扬芯片",
        "leader_change_pct": 20.01
      }
    ],
    "bottom": []
  },
  "limit_up_industries": [
    {"name": "汽车零部", "count": 10}
  ],
  "summary": {
    "tone": "偏强",
    "text": "市场偏强：上涨 3381 家 / 下跌 1647 家，涨停 84 家、跌停 3 家。",
    "up_ratio": 0.6478,
    "top_inflow": "半导体（+149.13 亿）",
    "top_outflow": "光伏设备（-20.47 亿）"
  },
  "warnings": [],
  "generated_at": "2026-11-11T19:53:03",
  "disclaimer": "市场复盘为实时数据快照，仅供研究参考，不构成投资建议。",
  "markdown": "## 市场复盘 2026-11-11 ..."
}
```

### 11.16 POST `/api/market/review/notify`

请求：

```json
{
  "date": "2026-11-11"
}
```

响应会附带：

```json
{
  "success": true,
  "notify": {
    "wecom": {"ok": true, "status": 200, "body": "..."},
    "skipped": "未配置任何通知渠道"
  }
}
```

### 11.17 GET `/api/market/sectors`

响应：

```json
{
  "success": true,
  "items": [
    {"name": "半导体", "code": "881121"},
    {"name": "白酒", "code": "881273"}
  ]
}
```

### 11.18 GET `/api/market/sector-history`

参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `name` | 必填 | 同花顺行业名称 |
| `days` | 60 | 5-250 |

响应：

```json
{
  "success": true,
  "name": "半导体",
  "as_of": "2026-11-11",
  "days": 60,
  "close": 13748.08,
  "change_1d": 3.09,
  "change_5d": 5.75,
  "change_20d": -3.54,
  "turnover_yi": 2109.6,
  "series": [
    {"date": "2026-11-11", "close": 13748.08}
  ],
  "disclaimer": "板块历史为同花顺行业指数，仅供研究参考，不构成投资建议。"
}
```

### 11.18.1 GET `/api/market/stock-search`

参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `q` | 空 | 代码或中文名称 |
| `limit` | 20 | 1-50 |

响应：

```json
{
  "success": true,
  "query": "茅台",
  "items": [
    {
      "ticker": "600519",
      "name": "贵州茅台",
      "exchange": "sh",
      "market": "沪市"
    }
  ]
}
```

### 11.18.2 GET `/api/market/hot-stocks`

参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `limit` | 30 | 1-100 |
| `date` | 当前/最近交易日 | `YYYY-MM-DD` |

响应：

```json
{
  "success": true,
  "date": "2026-11-11",
  "source": "limit_up",
  "count": 85,
  "items": [
    {
      "ticker": "603366",
      "name": "日出东方",
      "change_pct": 10.0,
      "price": 8.5,
      "turnover_yi": 12.3,
      "seal_fund_yi": 5.69,
      "streak": 4,
      "break_count": 1,
      "industry": "厨卫电器",
      "first_seal": "092500",
      "last_seal": "093257"
    }
  ],
  "disclaimer": "涨停池为实时快照，仅供研究参考，不构成投资建议。"
}
```

### 11.18.3 GET `/api/preview`

参数：

| 参数 | 说明 |
|---|---|
| `ticker` | 必填 |
| `end_date` | 可选 |
| `strategies` | 可选，逗号分隔 |

这是不调用 LLM 的规则化快速预览：

```json
{
  "success": true,
  "mode": "fast_preview",
  "ticker": "600519",
  "as_of": "2026-11-11",
  "price": {"close": 1402.3, "change_pct": -2.55},
  "summary": {
    "consensus": "偏多",
    "net_score": 30,
    "strength": 60,
    "triggered": 2
  },
  "preliminary_decision": "BUY",
  "decision_plan": {},
  "top_signals": [
    {
      "name": "放量突破",
      "direction": "bullish",
      "score": 80,
      "summary": "放量突破 20 日高点"
    }
  ],
  "markdown": "## 快速预览 600519 ...",
  "disclaimer": "快速预览为规则化初判，仅供研究参考，不构成投资建议。"
}
```

WebUI 点击“开始分析”后会并行请求该接口，把预览展示在研判进度页；
深度分析完成后自动隐藏预览并以最终 LLM 结果为准。

### 11.19 GET `/api/strategies/meta`

响应：

```json
{
  "success": true,
  "strategies": [
    {
      "key": "bull_trend",
      "name": "多头趋势",
      "category": "趋势",
      "description": "MA5、MA10、MA20 多头排列 + MA20 向上 + 低估值"
    }
  ],
  "default": [
    "bull_trend",
    "ma_golden_cross",
    "volume_breakout",
    "shrink_pullback",
    "bottom_volume",
    "box_oscillation"
  ]
}
```

### 11.20 GET `/api/strategies`

参数：

| 参数 | 说明 |
|---|---|
| `ticker` | 必填 |
| `end_date` | 可选 |
| `strategies` | 可选的逗号分隔策略 key |

响应核心字段：

```json
{
  "success": true,
  "ticker": "600519",
  "as_of": "2026-11-11",
  "bars": 250,
  "price": {
    "close": 1402.3,
    "change_pct": -2.55,
    "ma5": 1420.0,
    "ma10": 1430.0,
    "ma20": 1450.0,
    "rsi": 38.2,
    "volume_ratio": 1.2,
    "atr": 35.6,
    "recent_high20": 1500.0,
    "recent_low20": 1380.0
  },
  "summary": {
    "consensus": "偏多",
    "net_score": 20.0,
    "strength": 60,
    "bullish": 2,
    "bearish": 1,
    "triggered": 3,
    "total": 6
  },
  "selected_strategies": ["bull_trend", "volume_breakout"],
  "signals": [
    {
      "key": "volume_breakout",
      "name": "放量突破",
      "category": "量价",
      "triggered": true,
      "direction": "bullish",
      "score": 80,
      "summary": "放量突破 20 日高点",
      "reasons": ["量比 2.1", "突破 20 日高点"],
      "metrics": {}
    }
  ]
}
```

### 11.21 GET `/api/strategies/watchlist`

参数：`end_date`、`strategies`。

响应：

```json
{
  "success": true,
  "items": [
    {
      "ticker": "600519",
      "as_of": "2026-11-11",
      "close": 1402.3,
      "change_pct": -2.55,
      "consensus": "偏多",
      "strength": 60,
      "triggered": 2,
      "bullish": 2,
      "bearish": 0,
      "top": ["多头趋势", "放量突破"],
      "top_signals": [
        {
          "key": "volume_breakout",
          "name": "放量突破",
          "direction": "bullish",
          "score": 80,
          "summary": "放量突破 20 日高点",
          "reasons": ["量比 2.1"]
        }
      ]
    }
  ]
}
```

### 11.22 GET `/api/strategy-backtest`

参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `ticker` | 必填 | 股票代码 |
| `end_date` | 无 | 截止日期 |
| `strategies` | 默认核心 6 个 | 逗号分隔 |
| `days` | 250 | 60-300 |

响应核心字段：

```json
{
  "success": true,
  "ticker": "600519",
  "start_date": "2025-10-31",
  "end_date": "2026-11-11",
  "bars": 250,
  "benchmark_return": 0.0236,
  "strategies": [
    {
      "key": "volume_breakout",
      "name": "放量突破",
      "category": "量价",
      "description": "突破近 20 日高点 + 成交量放大",
      "direction": "bullish",
      "recent_direction": "bullish",
      "signal_count": 7,
      "forward": {
        "5": {"count": 7, "avg": 0.012, "win_rate": 0.71, "best": 0.05, "worst": -0.02},
        "10": {"count": 7, "avg": 0.0196, "win_rate": 0.83, "best": 0.08, "worst": -0.03},
        "20": {"count": 7, "avg": 0.021, "win_rate": 0.67, "best": 0.09, "worst": -0.04}
      },
      "equity": {
        "type": "long",
        "total_return": 0.1884,
        "max_drawdown": -0.08,
        "sharpe": 1.2,
        "trade_count": 6,
        "win_rate": 0.67,
        "final_equity": 118840.0,
        "equity_curve": [
          {"date": "2025-11-03", "equity": 100000.0}
        ]
      }
    }
  ]
}
```

### 11.23 GET `/api/strategy-alerts`

参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `tickers` | 无 | 逗号分隔；为空时读取自选股 |
| `end_date` | 无 | 截止日期 |
| `strategies` | 默认核心 6 个 | 逗号分隔 |
| `only_triggered` | true | 是否只保留触发标的 |

响应：

```json
{
  "success": true,
  "as_of": "2026-11-11",
  "items": [
    {
      "ticker": "600519",
      "name": "贵州茅台",
      "as_of": "2026-11-11",
      "close": 1402.3,
      "change_pct": -2.55,
      "consensus": "偏多",
      "strength": 60,
      "triggered": 2,
      "bullish": 2,
      "bearish": 0,
      "top": ["多头趋势", "放量突破"],
      "top_signals": [
        {
          "key": "volume_breakout",
          "name": "放量突破",
          "direction": "bullish",
          "score": 80,
          "summary": "放量突破 20 日高点",
          "reasons": ["量比 2.1"]
        }
      ]
    }
  ],
  "errors": [],
  "summary": {
    "total": 1,
    "triggered": 1,
    "bullish": 1,
    "bearish": 0,
    "neutral": 0,
    "signals": 2
  },
  "generated_at": "2026-11-11T19:53:03",
  "message": "发现 1 只自选股触发策略信号。",
  "disclaimer": "策略信号为规则化近似，仅供研究参考，不构成投资建议。"
}
```

### 11.24 POST `/api/strategy-alerts/notify`

请求：

```json
{
  "tickers": ["600519", "000001"],
  "end_date": "2026-11-11",
  "strategies": ["bull_trend", "volume_breakout"],
  "only_triggered": true,
  "notify_empty": false
}
```

默认没有触发信号时不推送；需要“无信号也通知”时设置 `notify_empty=true`。

### 11.25 GET `/api/watchlist`

```json
{
  "success": true,
  "items": [
    {
      "id": 1,
      "ticker": "600519",
      "name": "贵州茅台",
      "created_at": "2026-11-11T19:00:00"
    }
  ]
}
```

### 11.26 POST `/api/watchlist`

```json
{
  "ticker": "600519",
  "name": "贵州茅台"
}
```

### 11.27 DELETE `/api/watchlist/{ticker}`

```json
{
  "success": true,
  "message": "已删除"
}
```

### 11.28 GET `/api/reports`

参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `ticker` | 无 | 按股票过滤 |
| `limit` | 50 | 1-200 |
| `offset` | 0 | 偏移 |

响应：

```json
{
  "success": true,
  "reports": [
    {
      "id": 1,
      "ticker": "600519",
      "trade_date": "2026-11-11",
      "decision": "BUY",
      "decision_text": "...",
      "reports": {},
      "meta": {},
      "source": "webui",
      "created_at": "2026-11-11T19:00:00"
    }
  ]
}
```

### 11.29 GET `/api/reports/{id}`

```json
{
  "success": true,
  "report": {
    "id": 1,
    "ticker": "600519",
    "trade_date": "2026-11-11",
    "decision": "BUY",
    "decision_text": "...",
    "reports": {},
    "meta": {},
    "source": "webui",
    "created_at": "2026-11-11T19:00:00"
  }
}
```

### 11.30 DELETE `/api/reports/{id}`

```json
{
  "success": true,
  "message": "已删除"
}
```

### 11.31 GET `/api/daily-briefing`

参数：`date` 可选。

响应核心字段：

```json
{
  "success": true,
  "as_of": "2026-11-11",
  "requested_date": "",
  "summary": {
    "tone": "偏强",
    "up": 3381,
    "down": 1647,
    "limit_up": 84,
    "limit_down": 3,
    "alerts": 1,
    "signals": 2,
    "top_sector": "计算机、通信和其他电子设备制造业（+6.92%）",
    "top_concept": "钙钛矿（+8.48%）",
    "top_inflow": "半导体（+149.13 亿）",
    "top_outflow": "光伏设备（-20.47 亿）"
  },
  "review": {},
  "alerts": {},
  "warnings": [],
  "generated_at": "2026-11-11T19:53:03",
  "disclaimer": "本简报由规则化策略与实时行情生成，仅供研究参考，不构成投资建议。",
  "markdown": "# 每日盘后投研简报 2026-11-11 ..."
}
```

### 11.32 POST `/api/daily-briefing/notify`

请求：

```json
{
  "date": "2026-11-11"
}
```

服务端会：

1. 生成简报
2. 自动归档（`source=webui`）
3. 推送到已配置 Webhook

响应会包含 `briefing_id` 和 `notify`。

### 11.33 POST `/api/daily-briefing/archive`

请求：

```json
{
  "date": "2026-11-11"
}
```

响应：

```json
{
  "success": true,
  "as_of": "2026-11-11",
  "briefing_id": 3,
  "markdown": "# 每日盘后投研简报 2026-11-11 ..."
}
```

### 11.34 GET `/api/briefings`

参数：`limit`、`offset`。

响应：

```json
{
  "success": true,
  "items": [
    {
      "id": 3,
      "briefing_date": "2026-11-11",
      "as_of": "2026-11-11",
      "summary": {"tone": "偏强", "alerts": 1, "signals": 2},
      "source": "webui",
      "created_at": "2026-11-11T19:53:03",
      "updated_at": "2026-11-11T19:53:03"
    }
  ]
}
```

### 11.35 GET `/api/briefings/{id}`

```json
{
  "success": true,
  "briefing": {
    "id": 3,
    "briefing_date": "2026-11-11",
    "as_of": "2026-11-11",
    "summary": {},
    "source": "webui",
    "created_at": "2026-11-11T19:53:03",
    "updated_at": "2026-11-11T19:53:03",
    "markdown": "# 每日盘后投研简报 ..."
  }
}
```

### 11.36 DELETE `/api/briefings/{id}`

```json
{
  "success": true,
  "message": "已删除"
}
```

---

## 12. WebSocket 协议

连接：

```text
ws://127.0.0.1:8000/ws
```

客户端可发送：

```json
{"type": "ping"}
```

服务端返回：

```json
{"type": "pong"}
```

服务端推送类型：

| 类型 | 说明 |
|---|---|
| `status` | 当前分析状态 |
| `analysis_start` | 分析开始，包含 company/date/pipeline/strategies |
| `progress` | 进度消息，包含 node/message |
| `node_change` | 节点切换，包含 node/display/category |
| `report_ready` | 某个报告字段生成，包含 field/title/content/node |
| `message` | Agent 消息/工具调用，包含 node/display/role/text/tool_calls/tool_name |
| `analysis_complete` | 分析完成 |
| `error` | 分析失败 |
| `pong` | 心跳响应 |

`analysis_complete.data` 主要字段：

```json
{
  "decision": "BUY",
  "decision_text": "最终决策全文",
  "reports": {
    "market_report": "...",
    "sentiment_report": "...",
    "news_report": "...",
    "fundamentals_report": "...",
    "strategy_report": "...",
    "investment_plan": "...",
    "trader_investment_plan": "...",
    "final_trade_decision": "...",
    "decision_plan": "### 结构化决策卡 ..."
  },
  "company": "600519",
  "date": "2026-11-11",
  "report_id": 12,
  "pipeline": {},
  "strategies": ["bull_trend"],
  "decision_plan": {},
  "cancelled": false
}
```

---

## 13. 策略库说明

### 13.1 全部策略

| Key | 名称 | 分类 | 说明 |
|---|---|---|---|
| `bull_trend` | 多头趋势 | 趋势 | MA5/10/20 多头排列 + MA20 向上 + 低估值 |
| `ma_golden_cross` | 均线金叉 | 趋势 | MA5/10、MA10/20 金叉 + 量能确认 |
| `macd_cross` | MACD 金叉 | 趋势 | MACD 金叉信号 |
| `volume_breakout` | 放量突破 | 量价 | 突破近 20 日高点 + 成交量放大 |
| `shrink_pullback` | 缩量回踩 | 量价 | 回踩 MA10/MA20 + 量能萎缩 |
| `bottom_volume` | 底部放量 | 反转 | 接近 60 日低点 + 放量反弹 |
| `rsi_reversal` | RSI 超卖反转 | 反转 | RSI 低位拐头 |
| `boll_rebound` | 布林带下轨反弹 | 反转 | 布林带下轨附近企稳 |
| `dragon_head` | 龙头战法 | 强势 | 强势龙头，放量领涨 |
| `one_yang_three_yin` | 一阳夹三阴 | 形态 | 上涨趋势中的洗盘转强 |
| `box_oscillation` | 箱体震荡 | 形态 | 箱体低吸 |
| `platform_breakout` | 平台突破 | 形态 | 窄幅平台放量突破 |
| `chan_theory` | 缠论（简化） | 理论 | 简化笔/段/中枢与离开段 |
| `wave_theory` | 波浪理论（简化） | 理论 | 简化 5 浪上涨/调整结构 |
| `emotion_cycle` | 情绪周期 | 理论 | 冰点/修复/发酵/高潮/退潮 |

### 13.2 默认核心组合

```text
bull_trend
ma_golden_cross
volume_breakout
shrink_pullback
bottom_volume
box_oscillation
```

策略信号为规则化近似，缠论/波浪为简化实现，仅供研究参考。

---

## 14. 决策卡 / 市场复盘 / 每日简报结构

### 14.1 结构化决策卡字段

| 字段 | 说明 |
|---|---|
| `decision` | 最终决策 |
| `score` | 综合参考分 0-100 |
| `confidence` | 置信度 0-100 |
| `consensus` | 策略共识 |
| `entry` | 参考价 |
| `stop_loss` | 止损参考 |
| `take_profit` | 目标参考 |
| `risk_reward` | 风险收益比 |
| `position_pct` | 建议仓位（0-0.2） |
| `risk_pct` | 单笔风险比例 |
| `reasons` | 计算理由 |
| `disclaimer` | 免责声明 |

规则：

- 基于策略共识 + 最终决策 + ATR / 近 20 日高低点
- 持有/观望时建议仓位为 0
- 规则化估算，不构成投资建议

### 14.2 市场复盘字段

| 字段 | 说明 |
|---|---|
| `indices` | 指数列表 |
| `breadth` | 涨跌家数、涨跌停、活跃度 |
| `sectors.industry` | 行业领涨/领跌 |
| `sectors.concept` | 概念领涨/领跌 |
| `fund_flow` | 行业资金净流入/净流出 |
| `limit_up_industries` | 涨停行业分布 |
| `summary` | 规则化结论 |
| `warnings` | 数据源降级提示 |
| `markdown` | 可复制/导出的复盘文本 |

### 14.3 每日简报字段

| 字段 | 说明 |
|---|---|
| `summary` | 市场情绪、涨跌、预警数量、领涨行业/概念、资金流 |
| `review` | 完整市场复盘 |
| `alerts` | 自选股预警 |
| `warnings` | 数据提示 |
| `markdown` | 合并后的 Markdown |
| `briefing_id` | 归档后返回 |

### 14.4 简报归档字段

| 字段 | 说明 |
|---|---|
| `id` | 归档 ID |
| `briefing_date` | 简报日期 |
| `as_of` | 数据日期 |
| `summary` | 摘要 JSON |
| `source` | `webui` / `scheduler` |
| `created_at` | 创建时间 |
| `updated_at` | 更新时间 |
| `markdown` | 完整内容（详情接口） |

同一天重复归档会覆盖旧内容。

---

## 15. 定时任务与通知

### 15.1 定时任务

调度线程随 WebUI 启动。启用：

```bash
SCHEDULE_ENABLED=1
```

每日执行：

- 定时分析：`SCHEDULE_STOCKS`
- WebUI/API 定时任务：`scheduled_jobs` 表，按各自的 `schedule_time`
- 自选股预警：`SCHEDULE_ALERTS=1`
- 市场复盘：`SCHEDULE_REVIEW=1`
- 每日简报：`SCHEDULE_BRIEFING=1`

优先级：

```text
SCHEDULE_BRIEFING=1
  → 只发送合并简报
否则
  → 分别执行 SCHEDULE_ALERTS / SCHEDULE_REVIEW
```

`scheduled_jobs` 与 `SCHEDULE_STOCKS` 可以同时存在；WebUI 设置页可逐只股票添加、
暂停、启用或删除。两者都受 `SCHEDULE_ENABLED=1` 控制。

非交易日自动跳过预警、复盘、简报和数据库定时任务。

### 15.2 通知渠道

| 渠道 | 环境变量 | 格式 |
|---|---|---|
| 企业微信 | `WECOM_WEBHOOK_URL` | markdown |
| 飞书 | `FEISHU_WEBHOOK_URL` | text |
| 通用 Webhook | `GENERIC_WEBHOOK_URL` | `{title, text}` |

返回示例：

```json
{
  "wecom": {"ok": true, "status": 200, "body": "..."},
  "skipped": "未配置任何通知渠道"
}
```

### 15.3 空内容策略

- 自选股预警：默认无触发不推送
- 市场复盘：数据不可用时跳过
- 每日简报：市场与自选股都不可用时跳过

---

## 16. 数据源与降级策略

### 16.1 行情与财务

```text
AkShare → BaoStock        （默认，仅 A 股）
```

- `data_vendors` 中列出的供应商**就是完整的降级链**，未列出的供应商不会被调用
- 默认只配置 `akshare,baostock`，因此不会再触达 yfinance / Alpha Vantage
- `ALLOW_UNCONFIGURED_VENDORS=1` 可恢复旧行为（把所有可用供应商追加到链尾）
- `A_SHARE_ONLY=1`（默认）：非 A 股标的（AAPL / NVDA / 0700.HK）
  在取数前就被拒绝，不会触发美股数据源
- `ALLOW_SYNTHETIC_DATA=0`（默认）：不使用模拟数据兜底

### 16.2 市场复盘

| 数据 | 来源 |
|---|---|
| 指数 | 新浪指数 |
| 市场情绪 | 乐咕乐股 |
| 行业/概念热度 | 新浪行业/概念 |
| 行业资金流 | 同花顺行业资金流 |
| 涨停行业 | 东方财富涨停池 |
| 板块历史 | 同花顺行业指数 |

### 16.3 降级

- 单个数据源失败时保留其他数据
- `warnings` 中记录失败原因
- 不因单个接口失败导致整个复盘失败
- 东方财富 Push2 可能限流；系统优先使用新浪/同花顺接口

### 16.4 数据安全

- `ALLOW_SYNTHETIC_DATA=0` 时，真实数据全部失败会直接报错
- 不使用模拟数据生成投资结论
- 历史估值按报告可用日期过滤，避免未来函数

### 16.5 市场范围与启动行为

- **默认只做 A 股**：非 A 股标的在取数前被拒绝（`A_SHARE_ONLY=1`）
- **启动不自动分析**：不设置 `SCHEDULE_ENABLED=1` 时，WebUI 启动不会拉起
  调度器，也不会自动执行任何分析；任务队列也不会恢复上次遗留的排队任务
- 分析只能由用户在页面上点击“开始分析”，或显式调用 `POST /api/analyze` 触发
- `POST /api/analyze` 的 `company` 与 `date` 为**必填**，不再有
  `NVDA / 2024-05-10` 这类默认值


---

## 17. 测试与验证

常用命令：

```bash
uv run pytest
uv run ruff check .
uv run python -m compileall -q tradingagents cli webui
uv lock --check
```

前端脚本语法检查（先提取 `index.html` 中的内联 `<script>`，再执行）：

```bash
node --check <提取后的脚本.js>
```

当前测试状态：

```text
pytest: 203 passed
ruff: All checks passed
compileall: OK
uv lock --check: OK
```

覆盖范围包括：

- A 股代码与交易规则
- 交易日历
- 资金流/北向/龙虎榜
- 数据安全与工具
- 策略扫描与策略回测
- 决策卡
- 流水线预设与执行
- WebUI API
- Tavily
- LLM 鉴权
- 自选股预警
- 市场复盘
- 板块历史
- 每日简报
- 简报归档
- 定时任务
- 分析任务存储与任务 API
- 并行分析师与快速预览
- 定时分析任务 CRUD
- 股票搜索与涨停池
- 前端本地静态资源

---

## 18. 已知限制与注意事项

1. **策略为规则化实现**：缠论/波浪为简化版，不等同于完整理论。
2. **回测为简化模型**：默认无滑点，A 股多头/避险模拟，不做空。
3. **市场复盘是实时快照**：`date` 参数只用于提示，不回溯历史。
4. **板块历史来自同花顺行业指数**：与申万/中信行业分类可能不同。
5. **东方财富 Push2 可能限流**：系统已尽量使用新浪/同花顺替代，仍可能出现 warnings。
6. **无内置鉴权**：WebUI/API 默认本地使用；公网部署请加反向代理鉴权。
7. **LLM 成本与耗时**：深度模式会增加辩论轮次和调用次数。
8. **MiniMax 地址**：使用 `https://api.minimax.cn/anthropic`，不要使用 `api.minimaxi.cn`。
9. **通知为空**：未配置 Webhook 时不会报错，只返回 `skipped`。
10. **投资风险**：所有策略、回测、复盘和决策卡仅供研究参考，不构成投资建议。
11. **任务并发**：当前仍是单分析并发；任务会排队等待 `ANALYSIS_LOCK`，适合单用户/小团队。

---

## 19. 维护记录

| 日期 | 内容 |
|---|---|
| 2026-11-12 | 整理功能与 API 文档；补充策略回测、决策卡、预警、市场复盘、资金流、板块历史、每日简报、归档、定时任务与通知说明 |
| 2026-11-12 | 新增任务 API、股票搜索、涨停池热门股、前端本地静态资源；同步更新 API 与测试状态 |

后续建议维护方式：

- 新增 API 时同步更新第 10、11 节
- 新增环境变量时同步更新第 5 节
- 新增策略时同步更新第 13 节
- 新增定时任务/通知渠道时同步更新第 15 节
- 每次版本发布更新第 19 节和测试状态

---

> 本文档为项目内部使用说明，不构成任何投资建议。
