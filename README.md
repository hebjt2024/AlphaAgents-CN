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
| **三源容错** | AkShare → BaoStock → 本地缓存，逐级降级，网络不可用仍能出报告 |
| **智能限流** | 信号量并发控制 + 指数退避重试 + 请求超时保护 |
| **代码归一** | `600519` / `600519.SH` / `sh600519` / `SH600519` 全部识别为同一标的 |
| **名称解析** | 自动解析股票简称 → 代码，含别名与拼音映射 |
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

- **Web 工作台**：FastAPI + WebSocket 实时推送 Agent 进展，Token 级流式展示
- **CLI**：交互式终端，支持历史报告浏览与回看

---

## 🚀 快速开始

### 方式一：Web 工作台（推荐）

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

### 方式二：命令行

```bash
pip install -r requirements.txt
cp .env.example .env

# 交互式分析
alphaagents analyze

# 查看历史报告
alphaagents history

# 回看某次分析
alphaagents view
```

---

## ⚙️ 配置

### 模型服务

支持 OpenAI、Anthropic、Google、OpenRouter 及任何 OpenAI 兼容端点。
通过 `.env` 或 `tradingagents/default_config.py` 配置：

```python
"llm_provider": "anthropic",        # openai / anthropic / google / openrouter
"deep_think_llm": "MiniMax-M3",     # 深度思考模型（研究总监、组合经理）
"quick_think_llm": "MiniMax-M2.5",  # 快速模型（分析师并行阶段）
"backend_url": "https://api.minimaxi.com/anthropic",
"max_debate_rounds": 1,            # 多空辩论轮次
```

> 深度思考与快速思考分离是本项目的性能设计：分析师并行阶段跑在快模型上，
> 只有辩论与裁决环节使用强模型，可显著降低 token 成本。

### 数据源

无需配置即可运行，内置优先级：

```
AkShare  →  BaoStock  →  本地缓存
```

`akshare_adapter.py` 实现了限流、退避、超时与列名归一，`baostock_adapter.py`
提供历史数据兜底，`cache_provider.py` 在完全断网时仍可返回上次结果。

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
│   │   ├── akshare_adapter.py   # A 股数据适配
│   │   ├── baostock_adapter.py  # 历史数据兜底
│   │   ├── cache_provider.py    # 离线缓存兜底
│   │   └── interface.py         # 供应商路由
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
