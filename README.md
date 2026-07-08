<p align="center">
  <img src="assets/TauricResearch.png" style="width: 60%; height: auto;">
</p>

<div align="center" style="line-height: 1;">
  <a href="https://arxiv.org/abs/2412.20138" target="_blank"><img alt="arXiv" src="https://img.shields.io/badge/arXiv-2412.20138-B31B1B?logo=arxiv"/></a>
  <a href="https://discord.com/invite/hk9PGKShPK" target="_blank"><img alt="Discord" src="https://img.shields.io/badge/Discord-TradingResearch-7289da?logo=discord&logoColor=white&color=7289da"/></a>
  <a href="./assets/wechat.png" target="_blank"><img alt="WeChat" src="https://img.shields.io/badge/WeChat-TauricResearch-brightgreen?logo=wechat&logoColor=white"/></a>
  <a href="https://x.com/TauricResearch" target="_blank"><img alt="X Follow" src="https://img.shields.io/badge/X-TauricResearch-white?logo=x&logoColor=white"/></a>
  <br>
  <a href="https://github.com/TauricResearch/" target="_blank"><img alt="Community" src="https://img.shields.io/badge/Join_GitHub_Community-TauricResearch-14C290?logo=discourse"/></a>
</div>

<div align="center">
  <!-- Keep these links. Translations will automatically update with the README. -->
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=de">Deutsch</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=es">Español</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=fr">français</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=ja">日本語</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=ko">한국어</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=pt">Português</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=ru">Русский</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=zh">中文</a>
</div>

---

# TradingAgents: Multi-Agents LLM Financial Trading Framework

## News
- [2026-03] **TradingAgents v0.2.2** released with GPT-5.4/Gemini 3.1/Claude 4.6 model coverage, five-tier rating scale, OpenAI Responses API, Anthropic effort control, and cross-platform stability.
- [2026-02] **TradingAgents v0.2.0** released with multi-provider LLM support (GPT-5.x, Gemini 3.x, Claude 4.x, Grok 4.x) and improved system architecture.
- [2026-01] **Trading-R1** [Technical Report](https://arxiv.org/abs/2509.11420) released, with [Terminal](https://github.com/TauricResearch/Trading-R1) expected to land soon.

<div align="center">
<a href="https://www.star-history.com/#TauricResearch/TradingAgents&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=TauricResearch/TradingAgents&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=TauricResearch/TradingAgents&type=Date" />
   <img alt="TradingAgents Star History" src="https://api.star-history.com/svg?repos=TauricResearch/TradingAgents&type=Date" style="width: 80%; height: auto;" />
 </picture>
</a>
</div>

> 🎉 **TradingAgents** officially released! We have received numerous inquiries about the work, and we would like to express our thanks for the enthusiasm in our community.
>
> So we decided to fully open-source the framework. Looking forward to building impactful projects with you!

<div align="center">

🚀 [TradingAgents](#tradingagents-framework) | ⚡ [Installation & CLI](#installation-and-cli) | 🎬 [Demo](https://www.youtube.com/watch?v=90gr5lwjIho) | 📦 [Package Usage](#tradingagents-package) | 🤝 [Contributing](#contributing) | 📄 [Citation](#citation)

</div>

## TradingAgents Framework

TradingAgents is a multi-agent trading framework that mirrors the dynamics of real-world trading firms. By deploying specialized LLM-powered agents: from fundamental analysts, sentiment experts, and technical analysts, to trader, risk management team, the platform collaboratively evaluates market conditions and informs trading decisions. Moreover, these agents engage in dynamic discussions to pinpoint the optimal strategy.

<p align="center">
  <img src="assets/schema.png" style="width: 100%; height: auto;">
</p>

> TradingAgents framework is designed for research purposes. Trading performance may vary based on many factors, including the chosen backbone language models, model temperature, trading periods, the quality of data, and other non-deterministic factors. [It is not intended as financial, investment, or trading advice.](https://tauric.ai/disclaimer/)

Our framework decomposes complex trading tasks into specialized roles. This ensures the system achieves a robust, scalable approach to market analysis and decision-making.

### Analyst Team
- Fundamentals Analyst: Evaluates company financials and performance metrics, identifying intrinsic values and potential red flags.
- Sentiment Analyst: Analyzes social media and public sentiment using sentiment scoring algorithms to gauge short-term market mood.
- News Analyst: Monitors global news and macroeconomic indicators, interpreting the impact of events on market conditions.
- Technical Analyst: Utilizes technical indicators (like MACD and RSI) to detect trading patterns and forecast price movements.

<p align="center">
  <img src="assets/analyst.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

### Researcher Team
- Comprises both bullish and bearish researchers who critically assess the insights provided by the Analyst Team. Through structured debates, they balance potential gains against inherent risks.

<p align="center">
  <img src="assets/researcher.png" width="70%" style="display: inline-block; margin: 0 2%;">
</p>

### Trader Agent
- Composes reports from the analysts and researchers to make informed trading decisions. It determines the timing and magnitude of trades based on comprehensive market insights.

<p align="center">
  <img src="assets/trader.png" width="70%" style="display: inline-block; margin: 0 2%;">
</p>

### Risk Management and Portfolio Manager
- Continuously evaluates portfolio risk by assessing market volatility, liquidity, and other risk factors. The risk management team evaluates and adjusts trading strategies, providing assessment reports to the Portfolio Manager for final decision.
- The Portfolio Manager approves/rejects the transaction proposal. If approved, the order will be sent to the simulated exchange and executed.

<p align="center">
  <img src="assets/risk.png" width="70%" style="display: inline-block; margin: 0 2%;">
</p>

## Installation and CLI

### Installation

Clone TradingAgents:
```bash
git clone https://github.com/TauricResearch/TradingAgents.git
cd TradingAgents
```

Create a virtual environment in any of your favorite environment managers:
```bash
conda create -n tradingagents python=3.13
conda activate tradingagents
```

Install the package and its dependencies:
```bash
pip install .
```

### Required APIs

TradingAgents supports multiple LLM providers. Set the API key for your chosen provider:

```bash
export OPENAI_API_KEY=...          # OpenAI (GPT)
export GOOGLE_API_KEY=...          # Google (Gemini)
export ANTHROPIC_API_KEY=...       # Anthropic (Claude)
export XAI_API_KEY=...             # xAI (Grok)
export OPENROUTER_API_KEY=...      # OpenRouter
export ALPHA_VANTAGE_API_KEY=...   # Alpha Vantage
```

For local models, configure Ollama with `llm_provider: "ollama"` in your config.

Alternatively, copy `.env.example` to `.env` and fill in your keys:
```bash
cp .env.example .env
```

### CLI Usage

Launch the interactive CLI:
```bash
tradingagents          # installed command
python -m cli.main     # alternative: run directly from source
```
You will see a screen where you can select your desired tickers, analysis date, LLM provider, research depth, and more.

<p align="center">
  <img src="assets/cli/cli_init.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

An interface will appear showing results as they load, letting you track the agent's progress as it runs.

<p align="center">
  <img src="assets/cli/cli_news.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

<p align="center">
  <img src="assets/cli/cli_transaction.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

## TradingAgents Package

### Implementation Details

We built TradingAgents with LangGraph to ensure flexibility and modularity. The framework supports multiple LLM providers: OpenAI, Google, Anthropic, xAI, OpenRouter, and Ollama.

### Python Usage

To use TradingAgents inside your code, you can import the `tradingagents` module and initialize a `TradingAgentsGraph()` object. The `.propagate()` function will return a decision. You can run `main.py`, here's also a quick example:

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

ta = TradingAgentsGraph(debug=True, config=DEFAULT_CONFIG.copy())

# forward propagate
_, decision = ta.propagate("NVDA", "2026-01-15")
print(decision)
```

You can also adjust the default configuration to set your own choice of LLMs, debate rounds, etc.

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "openai"        # openai, google, anthropic, xai, openrouter, ollama
config["deep_think_llm"] = "gpt-5.2"     # Model for complex reasoning
config["quick_think_llm"] = "gpt-5-mini" # Model for quick tasks
config["max_debate_rounds"] = 2

ta = TradingAgentsGraph(debug=True, config=config)
_, decision = ta.propagate("NVDA", "2026-01-15")
print(decision)
```

See `tradingagents/default_config.py` for all configuration options.

## Contributing

We welcome contributions from the community! Whether it's fixing a bug, improving documentation, or suggesting a new feature, your input helps make this project better. If you are interested in this line of research, please consider joining our open-source financial AI research community [Tauric Research](https://tauric.ai/).

## Citation

Please reference our work if you find *TradingAgents* provides you with some help :)

```
@misc{xiao2025tradingagentsmultiagentsllmfinancial,
      title={TradingAgents: Multi-Agents LLM Financial Trading Framework}, 
      author={Yijia Xiao and Edward Sun and Di Luo and Wei Wang},
      year={2025},
      eprint={2412.20138},
      archivePrefix={arXiv},
      primaryClass={q-fin.TR},
      url={https://arxiv.org/abs/2412.20138}, 
}
```

---

# 项目架构详解与源码阅读指南

> 本文档面向 **零 Python 经验、零 LangChain 经验** 的读者。
> 阅读本指南前，强烈建议先阅读 [新手入门指南](tradingagents/新手入门指南.md)，
> 该指南覆盖了理解本项目所需的全部前置知识：Python 基础语法、LLM 概念、
> LangChain/LangGraph 框架详解、金融术语、设计模式基础。
> **读完入门指南后，你只需阅读本项目的代码注释即可完全理解代码，无需查阅其他资料。**

---

## 零、开始之前：你需要知道的核心概念

在开始阅读源码之前，你需要理解以下核心概念。每个概念的详细解释请参见 [新手入门指南](tradingagents/新手入门指南.md)。

### 0.1 什么是 LLM（大语言模型）？

LLM 是一种 AI 系统，能理解和生成人类语言。你可以把它理解为一个"超级聪明的对话机器人"。常见的有 GPT（OpenAI）、Claude（Anthropic）、Gemini（Google）。

**关键概念**：
- **Prompt（提示词）**：你发给 LLM 的文本。就像你向 ChatGPT 提问一样。
- **System Prompt（系统提示词）**：一段特殊的提示词，定义 AI 的"角色"和"行为规范"。例如："你是一位专业的市场分析师，负责分析技术指标..."
- **Tool Calling（工具调用）**：LLM 本身只能"说话"，不能获取实时数据。通过工具调用机制，你可以告诉 LLM "你有这些工具可用"，LLM 在需要时可以请求调用某个工具获取数据。

### 0.2 什么是 LangChain？

LangChain 是一个 Python 库，封装了与 LLM 交互的常用操作。类比：直接用 LLM API ≈ 手动写信寄信；用 LangChain ≈ 用电子邮件客户端。

**核心组件**：
- **ChatPromptTemplate**：提示词模板，预定义好"骨架"，运行时填入变量。比 f-string 更强大，支持消息类型和分步填充。
- **@tool 装饰器**：将普通 Python 函数转换为"智能体可调用的工具"。自动提取函数名、描述、参数 schema。
- **bind_tools()**：将工具信息"告诉"LLM，让 LLM 知道它可以调用这些工具。
- **| 管道操作符**：LangChain 的 LCEL（表达式语言），将前一步输出作为后一步输入，类似 Unix 管道 `|`。
- **invoke()**：LangChain 中"执行"操作的统一方法。

### 0.3 什么是 LangGraph？

LangGraph 是 LangChain 团队开发的"工作流编排框架"，将复杂的 AI 协作流程建模为"图"。类比：LangChain = 让一个 AI 更聪明；LangGraph = 让多个 AI 协作。

**核心概念**：
- **StateGraph**：状态图，核心类。创建图 → 添加节点 → 添加边 → 编译 → 执行。
- **节点（Node）**：图中的一个处理步骤。每个节点是一个函数，接收状态、返回状态更新。
- **边（Edge）**：节点之间的连接，表示数据流动方向。普通边是确定性的（A 完了一定执行 B）。
- **条件边（Conditional Edge）**：根据条件决定下一个节点。这是实现循环和分支的关键。
- **状态（State）**：在节点间传递的共享数据字典。所有节点都能读写这个状态。
- **ToolNode**：预置的特殊节点，自动执行 LLM 请求的工具调用。
- **START / END**：特殊节点，表示图的入口和出口。

### 0.4 关键设计模式

本项目使用了以下设计模式，详细解释参见 [新手入门指南](tradingagents/新手入门指南.md) 的"设计模式基础"章节：

| 模式 | 作用 | 出现在 |
|------|------|--------|
| 工厂模式 | 用一个函数统一创建不同类型的对象 | `factory.py`, `agents/__init__.py` |
| 策略模式 | 同一功能多种实现，运行时切换 | `interface.py`（数据源切换） |
| 责任链模式 | 按顺序尝试，失败则降级到下一个 | `interface.py`（数据源降级） |
| 抽象基类 | 定义接口规范，子类必须实现 | `base_client.py` |
| 闭包/工厂函数 | 创建可配置的函数对象 | `agent_utils.py` |
| 装饰器 | 不修改原函数的前提下添加功能 | `@tool`（所有工具文件） |

---

## 一、项目架构总览

TradingAgents 采用 **5 层分层架构**，每层有明确的职责：

```
┌─────────────────────────────────────────────────────────┐
│                    入口层 (Entry)                        │
│           main.py  /  cli/  (命令行界面)                 │
├─────────────────────────────────────────────────────────┤
│                   编排层 (Graph)                          │
│     trading_graph.py  setup.py  propagation.py           │
│     reflection.py  conditional_logic.py                 │
│     signal_processing.py                                │
│              ↕ 控制工作流和状态流转                        │
├─────────────────────────────────────────────────────────┤
│                  智能体层 (Agents)                        │
│   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│   │ 分析师(4) │ │ 研究员(2) │ │ 风险(3)  │ │ 交易员(1) │  │
│   │ 基本面    │ │ 多头      │ │ 激进     │ │          │  │
│   │ 市场      │ │ 空头      │ │ 保守     │ │ 管理者(2) │  │
│   │ 新闻      │ │           │ │ 中性     │ │ 研究经理  │  │
│   │ 社交媒体  │ │           │ │          │ │ 投资经理  │  │
│   └──────────┘ └──────────┘ └──────────┘ └──────────┘  │
│              ↕ 调用工具获取数据 / 调用 LLM 推理           │
├─────────────────────────────────────────────────────────┤
│                   数据层 (Dataflows)                      │
│     interface.py (数据路由)  y_finance.py (Yahoo)        │
│     alpha_vantage/ (Alpha Vantage 系列)                  │
│     stockstats_utils.py (技术指标计算)                    │
│              ↕ 从外部 API 获取金融数据                     │
├─────────────────────────────────────────────────────────┤
│                 LLM 客户端层 (LLM Clients)                 │
│     base_client.py (抽象基类)  factory.py (工厂)          │
│     openai_client.py  anthropic_client.py               │
│     google_client.py  validators.py                     │
│              ↕ 调用大语言模型 API                          │
└─────────────────────────────────────────────────────────┘
```

**核心设计思想**：模拟真实交易公司的组织架构，每个智能体扮演不同角色，通过协作和辩论做出投资决策。

---

## 二、从哪开始读？—— 源码阅读路线图

### 第一步：看整体流程（10分钟）

**先读 `main.py`**，这是最简单的入口，只有 30 行代码。你会看到：

- 如何创建配置（`DEFAULT_CONFIG.copy()`）
- 如何初始化 `TradingAgentsGraph`
- 如何调用 `propagate()` 进行分析
- 如何调用 `reflect_and_remember()` 进行反思

**再看 `default_config.py`**，了解所有可配置的参数及其含义。这是理解项目行为的"钥匙"。

### 第二步：理解核心工作流（30分钟）

**读 `tradingagents/graph/` 目录**，这是项目的"大脑"：

| 文件 | 作用 | 关键概念 |
|------|------|----------|
| `trading_graph.py` | 主编排类，创建并管理整个工作流 | `StateGraph`、`ToolNode`、双 LLM 架构 |
| `setup.py` | 搭建 LangGraph 图结构（节点+边） | `add_node`、`add_edge`、`add_conditional_edges` |
| `propagation.py` | 创建初始状态，启动分析流程 | `AgentState` 数据结构 |
| `conditional_logic.py` | 条件路由（判断下一步做什么） | 条件边、工具调用检测、辩论终止 |
| `reflection.py` | 事后反思和记忆存储 | 情景记忆、BM25 检索 |
| `signal_processing.py` | 从长文本中提取简洁决策 | BUY/HOLD/SELL 五级评分 |

**阅读顺序建议**：`trading_graph.py` → `propagation.py` → `setup.py` → `conditional_logic.py`

### 第三步：理解智能体角色（40分钟）

**读 `tradingagents/agents/` 目录**，每个智能体是一个独立的 Python 文件：

| 团队 | 角色 | 文件 | 职责 |
|------|------|------|------|
| 分析师 | 基本面分析师 | `analysts/fundamentals_analyst.py` | 分析财务报表、估值指标 |
| 分析师 | 市场分析师 | `analysts/market_analyst.py` | 分析技术指标和价格趋势 |
| 分析师 | 新闻分析师 | `analysts/news_analyst.py` | 分析新闻和宏观事件 |
| 分析师 | 社交媒体分析师 | `analysts/social_media_analyst.py` | 分析社交情绪 |
| 研究员 | 多头研究员 | `researchers/bull_researcher.py` | 看涨论证 |
| 研究员 | 空头研究员 | `researchers/bear_researcher.py` | 看跌论证 |
| 风险管理 | 激进/保守/中性 | `risk_mgmt/*.py` | 三方风险辩论 |
| 交易员 | 交易员 | `trader/trader.py` | 制定交易计划 |
| 管理者 | 研究经理 | `managers/research_manager.py` | 裁决多空辩论 |
| 管理者 | 投资组合经理 | `managers/portfolio_manager.py` | 最终决策 |

**关键知识点**：每个智能体文件都遵循相同的模式：
1. 定义系统提示词（system prompt），告诉 LLM 它扮演什么角色
2. 绑定工具（bind_tools），告诉智能体它能调用哪些数据函数
3. 创建节点函数（node function），供 LangGraph 工作流调用

### 第四步：理解数据获取（30分钟）

**读 `tradingagents/dataflows/` 目录**：

| 文件 | 作用 |
|------|------|
| `interface.py` | **核心路由器**：根据配置选择 yfinance 或 Alpha Vantage 数据源 |
| `y_finance.py` | Yahoo Finance 实现：价格、技术指标、基本面、内部交易 |
| `yfinance_news.py` | Yahoo Finance 新闻获取 |
| `stockstats_utils.py` | 技术指标计算（MACD、RSI、布林带等） |
| `alpha_vantage_common.py` | Alpha Vantage API 基础封装 |
| `alpha_vantage_*.py` | Alpha Vantage 各数据类型的实现 |

### 第五步：理解 LLM 调用（20分钟）

**读 `tradingagents/llm_clients/` 目录**：

| 文件 | 作用 |
|------|------|
| `base_client.py` | 抽象基类，定义 `get_llm()` 和 `validate_model()` 接口 |
| `factory.py` | 工厂函数，根据提供商名称创建对应客户端 |
| `openai_client.py` | OpenAI（及兼容的 Ollama、OpenRouter、xAI）客户端 |
| `anthropic_client.py` | Anthropic Claude 客户端 |
| `google_client.py` | Google Gemini 客户端 |
| `validators.py` | 模型名称白名单验证 |

---

## 三、核心概念详解

> 以下概念在代码注释中反复出现，理解它们是读懂源码的关键。
> 更详细的解释和代码示例请参见 [新手入门指南](tradingagents/新手入门指南.md)。

### 3.1 LangGraph 工作流（图的完整生命周期）

LangGraph 将多智能体协作建模为"有向图"。一个图从创建到执行经历的完整生命周期：

```
第 1 步：创建图
  workflow = StateGraph(AgentState)
  → AgentState 是一个 TypedDict，定义了所有节点共享的数据结构

第 2 步：添加节点
  workflow.add_node("Market Analyst", market_analyst_func)
  → 每个节点是一个函数：接收 state 字典，返回 state 更新字典

第 3 步：添加边
  workflow.add_edge(START, "Market Analyst")        → 普通边：A 完了一定执行 B
  workflow.add_edge("Market Analyst", "News Analyst")

第 4 步：添加条件边
  workflow.add_conditional_edges(
      "Market Analyst",              → 从哪个节点出发
      should_continue_market,       → 条件函数：返回字符串决定路由
      ["tools_market", "Msg Clear Market"]  → 可能的目标节点
  )
  → 条件函数返回 "tools_market" 时执行工具，返回 "Msg Clear Market" 时进入下一个分析师

第 5 步：编译
  graph = workflow.compile()
  → 验证图结构完整性（是否有孤立节点、是否可达 END），生成可执行对象

第 6 步：执行
  final_state = graph.invoke(initial_state)        → 一次性执行完
  for chunk in graph.stream(initial_state): ...    → 逐步返回每个节点的中间结果
```

TradingAgents 的完整工作流图：

```
                    ┌──────────┐
                    │  START   │
                    └────┬─────┘
                         │
              ┌──────────▼──────────┐
              │  基本面分析师        │
              └──────────┬──────────┘
                         │
              ┌──────────▼──────────┐
              │  市场分析师          │
              └──────────┬──────────┘
                         │
                    ...  (新闻/社交媒体分析师) ...
                         │
              ┌──────────▼──────────┐
              │  多头 ⇄ 空头 辩论    │  ← 循环 max_debate_rounds 轮
              └──────────┬──────────┘
                         │
              ┌──────────▼──────────┐
              │  研究经理 裁决       │
              └──────────┬──────────┘
                         │
              ┌──────────▼──────────┐
              │  交易员 制定计划     │
              └──────────┬──────────┘
                         │
              ┌──────────▼──────────┐
              │  激进⇄保守⇄中性 辩论 │  ← 循环 max_risk_discuss_rounds 轮
              └──────────┬──────────┘
                         │
              ┌──────────▼──────────┐
              │  投资组合经理 决策   │
              └──────────┬──────────┘
                         │
                    ┌────▼─────┐
                    │   END    │
                    └──────────┘
```

### 3.2 智能体如何工作（从代码到运行）

每个智能体文件都遵循统一的设计模式。以市场分析师为例，代码结构如下：

```python
# 第 1 步：定义工厂函数（接收 LLM 实例，返回节点函数）
def create_market_analyst(llm):

    # 第 2 步：定义节点函数（LangGraph 会调用这个函数）
    def market_analyst_node(state):
        # 读取状态
        current_date = state["trade_date"]

        # 第 3 步：定义系统提示词（告诉 LLM 它的角色和任务）
        system_message = "You are a trading assistant tasked with analyzing..."

        # 第 4 步：创建提示词模板（ChatPromptTemplate）
        prompt = ChatPromptTemplate.from_messages([
            ("system", "...{system_message}...{tool_names}..."),
            MessagesPlaceholder(variable_name="messages"),  # 历史消息插入点
        ])
        # partial()：预先填充部分变量（分步填表）
        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([t.name for t in tools]))

        # 第 5 步：构建链（| 管道操作符）
        # prompt 格式化消息 → llm.bind_tools(tools) 发给 LLM（附带工具信息）
        chain = prompt | llm.bind_tools(tools)

        # 第 6 步：执行链
        result = chain.invoke(state["messages"])

        # 第 7 步：返回状态更新
        # LangGraph 会将返回的字典合并到全局状态中
        return {"messages": [result], "market_report": report}

    return market_analyst_node  # 工厂函数返回节点函数
```

**运行时发生了什么？**

```
1. LangGraph 执行到 "Market Analyst" 节点
   → 调用 market_analyst_node(state)

2. market_analyst_node 读取 state["trade_date"] 等信息
   → 构建提示词

3. chain.invoke(state["messages"])
   → prompt 格式化消息
   → 发给 LLM，同时附带工具描述（bind_tools）
   → LLM 返回 AIMessage

4. LLM 可能返回 tool_calls（请求调用工具）
   → LangGraph 检测到 tool_calls
   → 路由到 ToolNode 执行工具
   → 工具结果返回给 LLM
   → LLM 再次分析（可能再次请求工具，或生成最终报告）

5. LLM 生成最终分析报告（无 tool_calls）
   → 返回 {"messages": [result], "market_report": report}
   → LangGraph 将 market_report 合并到全局状态

6. LangGraph 执行下一个节点（消息清除 → 下一个分析师）
```

每个智能体的本质是三要素的组合：

```
输入：当前状态（股票代码、日期、已有分析报告）
  │
  ▼
┌─────────────────────────────────────┐
│ 系统提示词（System Prompt）          │
│ "你是一位专业的市场分析师..."        │
│ 定义了智能体的角色、任务和行为规范    │
└─────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────┐
│ LLM（大语言模型）                    │
│ 根据提示词和输入，生成分析内容       │
│ 或者决定调用某个工具获取数据         │
└─────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────┐
│ 工具（Tools）                        │
│ get_stock_data() 获取价格数据       │
│ get_indicators() 获取技术指标       │
│ get_news() 获取新闻                 │
└─────────────────────────────────────┘
  │
  ▼
输出：分析报告（存入状态中，传给下一个智能体）
```

### 3.3 双 LLM 架构

项目使用两种 LLM 模型：
- **深度思考模型**（deep_think_llm）：用于复杂分析任务（如写报告、做决策），使用更强的模型
- **快速思考模型**（quick_think_llm）：用于简单任务（如数据格式化、简单判断），使用更快的模型

### 3.4 状态管理（AgentState 详解）

所有智能体共享一个 **状态字典**（`AgentState`）。这个字典在节点间传递，随着工作流推进逐步丰富。

**AgentState 的关键字段**（完整定义在 `agents/utils/agent_states.py`）：

```python
# AgentState 继承自 MessagesState（内置 messages 字段）
class AgentState(MessagesState):
    company_of_interest: str          # 目标股票代码，如 "NVDA"
    trade_date: str                   # 分析日期，如 "2024-05-10"
    sender: str                       # 消息发送者标识

    # 分析师报告（每个分析师完成后填入）
    market_report: str                # 市场分析师的报告
    sentiment_report: str             # 社交媒体分析师的报告
    news_report: str                  # 新闻分析师的报告
    fundamentals_report: str          # 基本面分析师的报告

    # 多空辩论状态（InvestDebateState 类型的嵌套字典）
    investment_debate_state: dict     # 包含 bull_history, bear_history, judge_decision 等
    investment_plan: str              # 研究经理的投资判断

    trader_investment_plan: str       # 交易员的交易计划

    # 风险辩论状态（RiskDebateState 类型的嵌套字典）
    risk_debate_state: dict           # 包含三方辩论历史和裁决
    final_trade_decision: str         # 最终交易决策
```

**状态如何流转（运行时示例）**：

```
初始状态:
  { company_of_interest: "NVDA", trade_date: "2024-05-10", messages: [...] }

→ 市场分析师执行后:
  { ..., market_report: "MACD 显示看涨信号，RSI 为 65..." }

→ 新闻分析师执行后:
  { ..., news_report: "英伟达发布新一代 GPU..." }

→ 多空辩论后:
  { ..., investment_debate_state: {
        bull_history: "看多理由：AI 需求强劲...",
        bear_history: "看空理由：估值过高...",
        judge_decision: "倾向看多"
    }}

→ 投资组合经理决策后:
  { ..., final_trade_decision: "基于综合分析，建议 BUY..." }
```

**关于 messages 字段的特殊行为**：
- `messages` 字段使用了 `add_messages` reducer（合并器）
- 当节点返回 `{"messages": [new_message]}` 时，新消息会 **追加** 到列表末尾
- 其他字段（如 `market_report`）使用默认行为：新值 **覆盖** 旧值
- 消息清除节点（`create_msg_delete()`）使用 `RemoveMessage` 按 id 删除旧消息

### 3.5 工具调用机制（@tool 和 bind_tools）

工具是 LLM 获取外部数据的唯一途径。本项目使用 LangChain 的工具系统：

```
第 1 步：定义工具函数（用 @tool 装饰器）
  @tool
  def get_stock_data(
      symbol: Annotated[str, "股票代码，如 AAPL"],     # Annotated 描述会传给 LLM
      start_date: Annotated[str, "起始日期 yyyy-mm-dd"],
  ) -> str:
      """获取指定股票的 OHLCV 价格数据"""                  # docstring 成为工具描述
      return route_to_vendor("get_stock_data", symbol, ...)

  # @tool 装饰器自动生成：
  # - name = "get_stock_data"
  # - description = "获取指定股票的 OHLCV 价格数据"
  # - args_schema = { symbol: {type: str, description: "股票代码..."}, ... }

第 2 步：将工具绑定到 LLM（bind_tools）
  llm_with_tools = llm.bind_tools([get_stock_data, get_indicators])
  # bind_tools 将工具的 schema 附加到 LLM 的每次请求中
  # LLM 据此知道：有哪些工具可用、每个工具需要什么参数

第 3 步：LLM 决定是否调用工具
  response = llm_with_tools.invoke("分析 AAPL 的市场数据")
  # LLM 可能返回:
  # response.tool_calls = [{"name": "get_stock_data", "args": {"symbol": "AAPL", ...}}]
  # 这表示 LLM 想要调用 get_stock_data 工具

第 4 步：ToolNode 执行工具调用
  # LangGraph 的 ToolNode 自动：
  # 1. 读取 response 中的 tool_calls
  # 2. 找到对应的工具函数并调用
  # 3. 将返回值包装为 ToolMessage
  # 4. 将 ToolMessage 追加到 messages 列表

第 5 步：LLM 看到工具结果，继续分析
  # LLM 看到数据后，可能：
  # - 请求调用更多工具（回到步骤 3）
  # - 生成最终分析报告（不再有 tool_calls）
```

### 3.6 数据源路由

`interface.py` 中的 `route_to_vendor()` 函数实现了智能数据源切换：

```
用户请求 get_stock_data("NVDA", "2024-01-01", "2024-05-10")
  │
  ▼
route_to_vendor("get_stock_data", ...)
  │
  ├── 检查 tool_vendors 配置（工具级别，优先级最高）
  ├── 检查 data_vendors 配置（类别级别）
  │
  ├── 如果配置为 yfinance → 调用 get_YFin_data_online()
  ├── 如果配置为 alpha_vantage → 调用 get_alpha_vantage_stock()
  │
  └── 如果主数据源失败（限流）→ 自动降级到备用数据源
```

---

## 四、Python 知识点索引

在阅读源码时，你会遇到以下 Python 概念，我们已经添加了详细注释：

| 概念 | 出现在哪些文件中 |
|------|-----------------|
| 抽象基类 (ABC) | `base_client.py` |
| 工厂模式 | `factory.py`, `agents/__init__.py` |
| 装饰器 (@tool, @wraps) | `core_stock_tools.py`, `cli/main.py` |
| 类型注解 (TypedDict, Annotated) | `agent_states.py`, `interface.py` |
| 列表推导式 | `base_client.py`, `y_finance.py` |
| Lambda 函数 | `y_finance.py`, `yfinance_news.py` |
| 闭包 | `agent_utils.py`, `cli/main.py` |
| 枚举 (Enum) | `cli/models.py` |
| 回调函数 (Callback) | `cli/stats_handler.py` |
| 策略模式 | `interface.py` |
| 责任链模式 | `interface.py` (降级机制) |
| LangGraph StateGraph | `trading_graph.py`, `setup.py` |
| LangChain Tool | `core_stock_tools.py` |
| Pandas DataFrame | `stockstats_utils.py`, `y_finance.py` |
| Pydantic BaseModel | `cli/models.py` |

---

## 五、常见问题

### Q: 为什么有这么多智能体？
A: 模拟真实交易公司的分工协作。每个智能体专注于一个领域（基本面、技术面、新闻、风险等），通过辩论和协作做出更全面的决策。

### Q: 智能体之间如何通信？
A: 通过共享的**状态字典**（AgentState）。每个智能体读取前一个智能体的输出，生成自己的分析，写入状态字典，传给下一个智能体。

### Q: 辩论是如何进行的？
A: 多头研究员和空头研究员轮流发表观点（每轮双方各说一次），由研究经理裁决。风险辩手（激进/保守/中性）也是如此，由投资组合经理做最终决策。

### Q: 数据从哪里来？
A: 默认使用 Yahoo Finance（免费，无需 API 密钥）。也可以配置 Alpha Vantage（需要免费 API 密钥）。数据会被缓存到本地，避免重复请求。

### Q: 如何添加新的智能体？
A: 1) 在 `agents/` 下创建新文件，定义系统提示词和节点函数；2) 在 `agents/__init__.py` 中导出；3) 在 `graph/setup.py` 中添加到工作流图中。

### Q: 如何添加新的数据源？
A: 1) 在 `dataflows/` 下创建新文件，实现数据获取函数；2) 在 `interface.py` 的 `VENDOR_METHODS` 中注册；3) 在 `default_config.py` 的 `data_vendors` 中添加选项。
