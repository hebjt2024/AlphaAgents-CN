"""
TradingAgents Web UI 后端服务
基于 FastAPI + WebSocket，提供实时流式分析进度推送

架构：
  前端 HTML → HTTP POST /api/analyze → 后端启动分析线程
  分析线程 → 线程安全队列 → 主事件循环 → WebSocket → 前端实时显示进度
  分析完成 → 最终结果通过 WebSocket 推送到前端

API 加固（见 webui/backend/{api_config,errors,security,middleware}.py）：
  * 鉴权：设置 ``WEBUI_API_KEY`` 后，所有 ``/api`` 路由都要求携带密钥
    （``X-API-Key`` 或 ``Authorization: Bearer``）；``/healthz``、``/readyz``、
    ``/metrics``、静态前端与 ``/ws`` 豁免。
  * 限流：按客户端 IP 的滑动窗口限流（``WEBUI_RATE_LIMIT``，默认 120 次/分）。
  * 错误信封：所有错误统一为
    ``{"success": false, "code", "message", "details", "request_id"}``。
  * 探针与指标：``/healthz`` 存活、``/readyz`` 就绪（数据库/调度器/磁盘）、
    ``/metrics`` Prometheus 文本格式。
  * 跨域：设置 ``WEBUI_ALLOWED_ORIGINS`` 后启用 CORS。

启动方式：
  python webui/run_webui.py
"""

import asyncio
import csv
import importlib
import io
import logging
import os
import secrets
import shutil
import sqlite3
import threading
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Literal

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

# 先加载 .env，再导入读取环境变量的 default_config
from tradingagents import bootstrap as _bootstrap  # noqa: F401
from tradingagents.dataflows.trade_calendar import (
    is_cn_symbol,
    validate_analysis_date,
)
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.intent_parser import parse_intent
from tradingagents.graph.pipeline_presets import (
    ANALYST_META,
    PIPELINE_PRESETS,
    build_pipeline_config,
)
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.scheduler import start_scheduler, stop_scheduler
from tradingagents.services.backtest import _load_prices, run_backtest
from tradingagents.services.job_queue import ensure_job_queue, get_job_queue
from tradingagents.storage import (
    add_scheduled_job,
    add_watch,
    delete_briefing,
    delete_job,
    delete_paper_state,
    delete_report,
    delete_scheduled_job,
    fail_job,
    finish_job,
    get_account,
    get_briefing,
    get_job,
    get_report,
    list_accounts,
    list_briefings,
    list_jobs,
    list_paper_accounts,
    list_reports,
    list_scheduled_jobs,
    list_watchlist,
    load_paper_state,
    mark_job_running,
    remove_watch,
    save_paper_state,
    save_report,
    update_scheduled_job,
)
from tradingagents.strategies import DEFAULT_STRATEGY_KEYS, STRATEGY_META
from webui.backend.api_config import ApiConfig, load_config
from webui.backend.errors import (
    ApiError,
    AuthError,
    ForbiddenError,
    NotFoundResponder,
    error_payload,
    register_exception_handlers,
    safe_exception_message,
)
from webui.backend.middleware import (
    AuditMiddleware,
    ErrorStatusMiddleware,
    RateLimitMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
    TimingMiddleware,
    metrics,
)
from webui.backend.security import (
    api_key_scheme,
    bearer_scheme,
    current_user,
    require_api_key,
    require_risk_ack,
    require_role,
)

# 各 LLM 提供商的默认 API 地址。
# 之前 WebUI 只切换 provider/model，却沿用 DEFAULT_CONFIG 里的 MiniMax 地址，
# 导致选择 OpenAI/Google 后仍然请求 MiniMax 端点。
PROVIDER_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "google": "https://generativelanguage.googleapis.com/v1",
    "anthropic": "https://api.anthropic.com/",
    "xai": "https://api.x.ai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
}

# WebUI 切换 provider 时的模型默认值；anthropic 使用 .env/配置中的默认模型
PROVIDER_DEFAULT_MODELS = {
    "anthropic": {
        "deep": DEFAULT_CONFIG.get("deep_think_llm", "MiniMax-M3"),
        "quick": DEFAULT_CONFIG.get("quick_think_llm", "MiniMax-M3.1-Flash"),
    },
    "openai": {"deep": "gpt-4.1", "quick": "gpt-4o-mini"},
    "google": {"deep": "gemini-2.5-pro", "quick": "gemini-2.5-flash"},
    "xai": {"deep": "grok-4-0709", "quick": "grok-4-1-fast-non-reasoning"},
    "openrouter": {
        "deep": "z-ai/glm-4.5-air:free",
        "quick": "z-ai/glm-4.5-air:free",
    },
    "ollama": {"deep": "qwen3:latest", "quick": "qwen3:latest"},
}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _validate_backend_url(url: str | None) -> None:
    """限制请求级 backend_url，避免把 API Key 发到任意地址。

    允许：已知 provider 域名、配置中的默认 backend_url、回环地址。
    这套白名单可以覆盖自建 OpenAI 兼容端点（把地址写进 .env），
    但不允许 analyst 在请求里临时指向攻击者服务器。
    """
    if not url:
        return
    from urllib.parse import urlparse

    parsed = urlparse(str(url))
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("backend_url 必须是 http/https 地址")
    host = parsed.hostname.lower()
    allowed = {
        urlparse(item).hostname
        for item in PROVIDER_BASE_URLS.values()
        if urlparse(item).hostname
    }
    configured = DEFAULT_CONFIG.get("backend_url")
    if configured:
        configured_host = urlparse(str(configured)).hostname
        if configured_host:
            allowed.add(configured_host.lower())
    allowed.update({"localhost", "127.0.0.1", "::1"})
    if host not in allowed:
        raise ValueError(
            f"backend_url 域名不在白名单内：{host}；"
            "请把自建端点写入 .env 的 TRADINGAGENTS_BACKEND_URL"
        )


def resolve_backend_url(provider: str, model: str, requested: str | None) -> str | None:
    """按 provider/model 解析 API 地址，允许请求显式覆盖。"""
    if requested:
        return requested
    provider = (provider or "").lower()
    # MiniMax 的 Anthropic 兼容端点：优先使用 .env 中的默认地址
    if provider == "anthropic" and "minimax" in (model or "").lower():
        return (
            DEFAULT_CONFIG.get("backend_url")
            or "https://api.minimax.cn/anthropic"
        )
    return PROVIDER_BASE_URLS.get(provider)


# ============================================================================
# FastAPI 应用初始化
# ============================================================================
# 受保护的 API 路由：所有 /api 前缀的路由都挂在 api_router 上，
# 鉴权依赖挂在路由器级别（而不是逐个路由），新增路由默认就是受保护的。
# api_key_scheme / bearer_scheme 只用于在 OpenAPI 里声明安全方案，
# 真正的校验由 require_api_key 完成（这样鉴权失败也能走统一错误信封）。
api_router = APIRouter(
    dependencies=[
        Depends(require_api_key),
        Depends(api_key_scheme),
        Depends(bearer_scheme),
    ]
)

#: 登录相关路由不能挂 require_api_key（否则无法首次登录）。
auth_router = APIRouter()

active_connections: set[WebSocket] = set()
_main_loop: asyncio.AbstractEventLoop | None = None

analysis_status = {
    "running": False,
    "company": "",
    "date": "",
    "start_time": None,
    "job_id": "",
}

# 分析任务取消事件：run_analysis_sync 在每次 graph.stream 迭代前检查此标志，
# 若被设置则提前退出循环，实现优雅终止（而非 kill 线程）
# 由 /api/stop 端点设置，由 run_analysis_sync 在 finally 中清除
analysis_cancel_event = threading.Event()

# ============================================================================
# 分析任务队列参数
#
# WHY：此前用一个进程级互斥锁把整个系统串行化（同时只能跑一个分析），
# 且"是否在运行"的标志是在取锁之后才置位 —— 并发请求会全部看到空闲并
# 全部返回"已启动"，然后全部阻塞在锁上。现在改为有界 worker 队列：
# 超出容量才拒绝，而不是让请求无限堆积。
# ============================================================================

#: 分析任务的并发 worker 数（每个 worker 跑一次完整的多智能体分析）。
ANALYSIS_WORKERS = int(os.getenv("WEBUI_ANALYSIS_WORKERS", "1"))

#: 允许同时处于运行 + 排队状态的分析任务数上限。
MAX_PENDING_ANALYSES = int(os.getenv("WEBUI_MAX_PENDING_ANALYSES", "3"))

#: 模拟盘状态是"读-改-写"的 JSON 快照，必须串行化，避免两个请求互相覆盖。
PAPER_STATE_LOCK = threading.Lock()


# ============================================================================
# 请求/响应模型
# ============================================================================

class AnalyzeRequest(BaseModel):
    # 标的与日期改为**必填**。
    #
    # WHY：此前默认值是 company="NVDA"、date="2024-05-10"。任何只发了
    # 部分字段（或空 body）的请求都会被静默地变成"分析 NVDA 2024-05-10"——
    # 既会烧掉一次 LLM 额度，也会把美股数据混进 A 股系统。现在缺字段
    # 会直接返回 422，而不是替用户决定分析什么。
    company: str = Field(..., min_length=1, description="股票代码或名称")
    date: str = Field(..., min_length=1, description="分析日期 YYYY-MM-DD")
    horizon: str = "medium"
    llm_provider: str = DEFAULT_CONFIG.get("llm_provider", "anthropic")
    deep_think_llm: str = DEFAULT_CONFIG.get("deep_think_llm", "MiniMax-M3")
    quick_think_llm: str = DEFAULT_CONFIG.get(
        "quick_think_llm", "MiniMax-M3.1-Flash"
    )
    backend_url: str | None = None
    max_debate_rounds: int = DEFAULT_CONFIG.get("max_debate_rounds", 1)
    max_risk_discuss_rounds: int = DEFAULT_CONFIG.get(
        "max_risk_discuss_rounds", 1
    )
    # 自定义图编排
    pipeline_preset: str = "standard"
    analysts: list[str] = Field(
        default_factory=lambda: ["market", "social", "news", "fundamentals"]
    )
    enable_debate: bool = True
    enable_trader: bool = True
    enable_risk: bool = True
    # 本次分析启用的策略 key（不是每次都跑全部策略）
    strategies: list[str] = Field(
        default_factory=lambda: list(DEFAULT_STRATEGY_KEYS)
    )


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8)


class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=8)
    role: str = "analyst"
    display_name: str = ""


class UserUpdateRequest(BaseModel):
    role: str | None = None
    enabled: bool | None = None
    display_name: str | None = None
    password: str | None = Field(default=None, min_length=8)


class WatchRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=20)
    name: str = Field(default="", max_length=40)

    @field_validator("ticker")
    @classmethod
    def _safe_ticker(cls, value: str) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("股票代码不能为空")
        if any(char in text for char in "<>\"'`\\;"):
            raise ValueError("股票代码包含非法字符")
        return text


class IntentRequest(BaseModel):
    query: str


class LLMTestRequest(BaseModel):
    llm_provider: str = DEFAULT_CONFIG.get("llm_provider", "anthropic")
    model: str = DEFAULT_CONFIG.get("quick_think_llm", "MiniMax-M3.1-Flash")
    backend_url: str | None = None


class BacktestRequest(BaseModel):
    ticker: str
    start_date: str
    end_date: str
    strategy: Literal["ma_cross", "buy_hold"] = "ma_cross"
    fast: int = 5
    slow: int = 20
    initial_cash: float = 100000.0
    commission_rate: float = 0.0003
    stamp_duty_rate: float = 0.0005
    slippage_rate: float = 0.001


class StrategyAlertNotifyRequest(BaseModel):
    tickers: list[str] | None = None
    end_date: str | None = None
    strategies: list[str] | None = None
    only_triggered: bool = True
    notify_empty: bool = False


class MarketReviewNotifyRequest(BaseModel):
    date: str | None = None


class DailyBriefingNotifyRequest(BaseModel):
    date: str | None = None


class ScheduledJobRequest(BaseModel):
    ticker: str
    name: str = ""
    schedule_time: str = "18:00"


class ScheduledJobUpdateRequest(BaseModel):
    name: str | None = None
    schedule_time: str | None = None
    enabled: bool | None = None


class EvaluationRequest(BaseModel):
    """手动触发一次推荐评估（可选回写记忆）。"""

    ticker: str | None = None
    limit: int = Field(default=50, ge=1, le=500)
    horizons: list[int] | None = None
    write_memory: bool = False


class PortfolioAccountRequest(BaseModel):
    account: str | None = None


class PortfolioSnapshotRequest(PortfolioAccountRequest):
    date: str | None = None


class PortfolioRiskRequest(BaseModel):
    account: str | None = None
    lookback: int = Field(default=252, ge=60, le=1000)
    benchmark: str = "000300"
    rebalance_method: str = "equal"


class RebalanceRequest(BaseModel):
    account: str | None = None
    method: str = "equal"
    max_single_weight: float | None = Field(default=None, gt=0, le=1)
    max_total_weight: float | None = Field(default=None, gt=0, le=1)


class PaperResetRequest(BaseModel):
    account: str | None = None
    initial_cash: float = Field(default=100000.0, gt=0)


class PaperOrderRequest(BaseModel):
    ticker: str = Field(..., min_length=1)
    side: str = Field(..., min_length=1)
    quantity: int = Field(..., gt=0)
    order_type: str = "market"
    limit_price: float | None = Field(default=None, gt=0)
    name: str = ""
    reason: str = ""
    note: str = ""
    stop_loss: float = 0.0
    take_profit: float = 0.0
    account: str | None = None


class PaperPlanRequest(BaseModel):
    ticker: str = Field(..., min_length=1)
    plan: dict[str, Any] = Field(default_factory=dict)
    name: str = ""
    account: str | None = None


class PaperMarkRequest(BaseModel):
    account: str | None = None
    date: str | None = None
    prices: dict[str, float] | None = None


class PaperAdvanceRequest(BaseModel):
    account: str | None = None
    start_date: str
    end_date: str


class DataSyncRequest(BaseModel):
    """同步一只或多只标的到本地行情库。"""

    ticker: str | None = None
    tickers: list[str] | None = None
    start_date: str | None = None
    end_date: str | None = None
    adjust: str = "qfq"
    force: bool = False


class AlertRuleRequest(BaseModel):
    ticker: str = Field(..., min_length=1)
    rule_type: str = Field(..., min_length=1)
    threshold: float
    name: str = ""
    cooldown_minutes: int = Field(default=30, ge=1, le=1440)
    note: str = ""
    enabled: bool = True


class AlertRuleUpdateRequest(BaseModel):
    threshold: float | None = None
    cooldown_minutes: int | None = Field(default=None, ge=1, le=1440)
    enabled: bool | None = None
    name: str | None = None
    note: str | None = None


class AlertCheckRequest(BaseModel):
    tickers: list[str] | None = None
    notify: bool = False
    include_portfolio: bool = True
    account: str | None = None


class SocialSnapshotRequest(BaseModel):
    ticker: str = Field(..., min_length=1)
    name: str = ""
    include_xueqiu: bool = False


class ScreenerRequest(BaseModel):
    top: int = Field(default=30, ge=1, le=200)
    style: str = "balanced"
    min_amount_yi: float = Field(default=1.0, ge=0)
    min_market_cap_yi: float = Field(default=30.0, ge=0)
    max_market_cap_yi: float | None = Field(default=None, ge=0)
    exclude_st: bool = True
    exclude_limit: bool = True
    exclude_loss: bool = False
    boards: list[str] | None = None
    include_financials: bool = True
    include_fund_flow: bool = False
    industry_neutral: bool = False
    save: bool = True


# ---------------------------------------------------------------------------
# 响应模型
# ---------------------------------------------------------------------------
# 只为“形状稳定”的接口声明 response_model；无法用模型表达既有载荷的接口
# 宁可留空，也不改变既有返回结构。


class HealthResponse(BaseModel):
    """存活探针响应。"""

    status: str


class ReadinessCheck(BaseModel):
    """就绪探针的单项检查结果。"""

    name: str
    status: str
    required: bool
    detail: str


class ReadyResponse(BaseModel):
    """就绪探针响应。"""

    status: str
    ready: bool
    checks: list[ReadinessCheck]
    version: str
    uptime_seconds: float


class EnvelopeResponse(BaseModel):
    """响应模型基类：允许保留错误信封的附加字段。

    ``extra="allow"`` 让业务失败分支返回的 ``code`` / ``details`` /
    ``request_id`` 不会在响应模型序列化时被丢掉；同时成功分支的载荷结构
    保持原样（模型不会凭空增加字段）。
    """

    model_config = ConfigDict(extra="allow")


class SuccessMessageResponse(EnvelopeResponse):
    """``{"success": bool, "message": str}`` 形态的通用响应。"""

    success: bool
    message: str


class ItemListResponse(EnvelopeResponse):
    """``{"success": true, "items": [...]}`` 形态的列表响应。"""

    success: bool
    items: list[dict[str, Any]]


class WatchlistItemResponse(EnvelopeResponse):
    """``{"success": true, "item": {...}}`` 形态的自选股响应。"""

    success: bool
    item: dict[str, Any]


class ReportListResponse(EnvelopeResponse):
    """报告列表响应。"""

    success: bool
    reports: list[dict[str, Any]]


class RunListResponse(EnvelopeResponse):
    """运行指标列表响应。"""

    success: bool
    runs: list[dict[str, Any]]


class AggregateResponse(EnvelopeResponse):
    """聚合指标响应。"""

    success: bool
    aggregate: dict[str, Any]


class PortfolioResponse(EnvelopeResponse):
    """组合快照响应。"""

    success: bool
    portfolio: dict[str, Any]


class EvaluationResponse(EnvelopeResponse):
    """组合级评估响应。"""

    success: bool
    evaluation: dict[str, Any]


class SchedulerStatusResponse(EnvelopeResponse):
    """调度器状态响应。"""

    enabled: str
    time: str
    stocks: str
    scheduled_jobs: int


class PipelinePresetsResponse(EnvelopeResponse):
    """图编排预设响应。"""

    success: bool
    presets: dict[str, Any]
    analysts: list[dict[str, Any]]


class StrategyMetaResponse(EnvelopeResponse):
    """策略元数据响应。"""

    success: bool
    strategies: list[dict[str, Any]]
    default: list[str]


class TradeCalendarResponse(EnvelopeResponse):
    """交易日校验响应。"""

    success: bool
    is_trading_day: bool
    is_cn_symbol: bool
    message: str
    suggested_date: str | None


# ============================================================================
# 线程安全广播
# ============================================================================

def broadcast_threadsafe(message: dict):
    """从后台线程安全地向所有 WebSocket 客户端广播消息"""
    if _main_loop is None:
        return
    asyncio.run_coroutine_threadsafe(_broadcast_async(message), _main_loop)


async def _broadcast_async(message: dict):
    """异步广播消息到所有 WebSocket 客户端"""
    disconnected = set()
    for ws in active_connections:
        try:
            await ws.send_json(message)
        except Exception:
            disconnected.add(ws)
    active_connections.difference_update(disconnected)


# ============================================================================
# WebSocket 端点
# ============================================================================

async def websocket_endpoint(websocket: WebSocket):
    global _main_loop
    _main_loop = asyncio.get_running_loop()

    # 浏览器无法为 WebSocket 自定义请求头，因此用查询参数传递 API 密钥 / 会话 token。
    config = load_config()
    if config.auth_enabled:
        provided = websocket.query_params.get("api_key") or ""
        valid = False
        if config.api_key and provided and secrets.compare_digest(
            provided.encode("utf-8"), config.api_key.encode("utf-8")
        ):
            valid = True
        if not valid and config.multi_user and provided:
            from webui.backend.users import get_session_user

            valid = bool(get_session_user(provided))
        if not valid:
            await websocket.close(code=1008, reason="invalid api key")
            return

    await websocket.accept()
    active_connections.add(websocket)
    try:
        await websocket.send_json({
            "type": "status",
            "data": analysis_status,
        })
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        active_connections.discard(websocket)


# ============================================================================
# 消息格式化
# ============================================================================

# 工具调用参数的简化展示：从 tool_calls 中提取关键参数，便于用户理解
# 例如 get_stock_data(symbol, start_date, end_date) 只显示这三个参数
def _summarize_tool_call(tc: dict) -> str:
    """将一个 tool_call 字典格式化为人类可读的简短描述"""
    try:
        name = tc.get("name", "unknown")
        args = tc.get("args", {}) or {}
        if not isinstance(args, dict):
            return name
        # 针对常见工具挑选关键参数显示
        key_candidates = [
            "symbol", "ticker", "start_date", "end_date",
            "curr_date", "look_back_days", "limit",
        ]
        parts = []
        for k in key_candidates:
            if k in args and args[k] is not None:
                v = str(args[k])
                if len(v) > 40:
                    v = v[:37] + "..."
                parts.append(f"{k}={v}")
        if parts:
            return f"{name}({', '.join(parts)})"
        # 退化：显示所有参数但截断
        all_parts = []
        for k, v in args.items():
            v_str = str(v)
            if len(v_str) > 40:
                v_str = v_str[:37] + "..."
            all_parts.append(f"{k}={v_str}")
        if all_parts:
            return f"{name}({', '.join(all_parts)})"
        return name
    except Exception:
        return tc.get("name", "unknown")


def _extract_text(content) -> str:
    """从 AIMessage.content 中提取纯文本（兼容 str 和 list 格式）"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif "text" in item:
                    parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content)


def format_message_content(msg) -> dict:
    """
    将 langchain 消息对象格式化为前端可用的结构化字典。

    返回字段：
        role:       "human" | "ai" | "tool" | "system"
        text:       消息的纯文本主体（AI 消息的思考内容、工具返回内容等）
        tool_calls: AI 调用工具时，工具调用的简短描述列表
        tool_name:  工具消息的工具名
        is_empty:   AI 消息是否仅是工具调用占位（无文本内容）

    设计说明：
        - 不再粗暴截断 ToolMessage（前端会用展开/折叠处理长文本）
        - 不再把 [AI 调用工具: xxx] 拼到 text 中，而是单独放在 tool_calls
          字段，前端可分别渲染
        - AI 消息若仅触发工具调用而无文本，text 为空字符串，前端不显示空气泡
    """
    try:
        msg_type = type(msg).__name__
        if msg_type == "HumanMessage":
            return {"role": "human", "text": _extract_text(msg.content), "tool_calls": [], "is_empty": False}

        elif msg_type == "AIMessage":
            text = _extract_text(msg.content)
            tool_calls = []
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                tool_calls = [_summarize_tool_call(tc) for tc in msg.tool_calls]
            is_empty = (not text.strip()) and (not tool_calls)
            return {
                "role": "ai",
                "text": text,
                "tool_calls": tool_calls,
                "is_empty": is_empty,
            }

        elif msg_type == "ToolMessage":
            tool_name = getattr(msg, "name", "unknown") or "unknown"
            text = _extract_text(msg.content)
            # 检测是否为降级到 cache 的模拟/缓存数据
            # cache_provider 会在内容头部加 "# WARNING: This is simulated data for testing"
            # 或 "(cached)" 标记，这些数据非真实行情，需在前端醒目提示
            is_simulated = bool(text) and (
                "simulated data for testing" in text
                or "(cached)" in text
                or "(generated)" in text
            )
            return {
                "role": "tool",
                "text": text,
                "tool_calls": [],
                "tool_name": tool_name,
                "is_simulated": is_simulated,
                "is_empty": False,
            }

        else:
            return {
                "role": "system",
                "text": str(getattr(msg, "content", msg)),
                "tool_calls": [],
                "is_empty": False,
            }
    except Exception as e:
        return {
            "role": "system",
            "text": f"<消息格式化失败: {e}>",
            "tool_calls": [],
            "is_empty": False,
        }


def extract_node_name(chunk: dict) -> str:
    """从 chunk 中提取当前发言节点名（基于最后一条消息的 name 属性）"""
    if "messages" in chunk and chunk["messages"]:
        last_msg = chunk["messages"][-1]
        if hasattr(last_msg, "name") and last_msg.name:
            return last_msg.name
    return "Processing"


# 节点名 → 中文显示名映射（键已对齐 setup.py 中真实节点名）
# 修正点：
#   - "Social Media Analyst" → "Social Analyst"（setup.py 用 f"{analyst_type.capitalize()} Analyst" 生成）
#   - "Aggressive" / "Conservative" / "Neutral" → 加 " Analyst" 后缀
#   - 新增 Bull/Bear Researcher、Research Manager、Trader、Portfolio Manager、Msg Clear 等
def node_display_name(node_name: str) -> str:
    name_map = {
        # 策略扫描节点
        "Strategy Scanner": "策略扫描",
        "Parallel Analysts": "分析团队（并行）",
        # 分析师节点（与 setup.py 中 add_node 名称严格一致）
        "Market Analyst": "市场分析师",
        "Social Analyst": "社交媒体分析师",
        "News Analyst": "新闻分析师",
        "Fundamentals Analyst": "基本面分析师",
        # 工具节点
        "tools_market": "获取市场数据",
        "tools_social": "获取社交媒体数据",
        "tools_news": "获取新闻数据",
        "tools_fundamentals": "获取基本面数据",
        # 消息清理节点
        "Msg Clear Market": "清理市场消息",
        "Msg Clear Social": "清理社交媒体消息",
        "Msg Clear News": "清理新闻消息",
        "Msg Clear Fundamentals": "清理基本面消息",
        # 研究员辩论
        "Bull Researcher": "多头研究员",
        "Bear Researcher": "空头研究员",
        "Research Manager": "研究经理",
        # 交易员
        "Trader": "交易员",
        # 风险辩论（注意：实际节点名带 " Analyst" 后缀）
        "Aggressive Analyst": "激进型风险分析师",
        "Conservative Analyst": "保守型风险分析师",
        "Neutral Analyst": "中立型风险分析师",
        # 投资组合经理
        "Portfolio Manager": "投资组合经理",
        # 兜底
        "Processing": "处理中",
    }
    return name_map.get(node_name, node_name)


# 节点分类，前端可据此选择不同的视觉样式与折叠策略
def node_category(node_name: str) -> str:
    """返回节点类别：analyst / tool / clearer / researcher / manager / trader / risk / portfolio / system"""
    if node_name == "Strategy Scanner":
        return "strategy"
    if node_name == "Parallel Analysts":
        return "analyst"
    if node_name in ("Market Analyst", "Social Analyst", "News Analyst", "Fundamentals Analyst"):
        return "analyst"
    if node_name.startswith("tools_"):
        return "tool"
    if node_name.startswith("Msg Clear"):
        return "clearer"
    if node_name in ("Bull Researcher", "Bear Researcher"):
        return "researcher"
    if node_name == "Research Manager":
        return "manager"
    if node_name == "Trader":
        return "trader"
    if node_name in ("Aggressive Analyst", "Conservative Analyst", "Neutral Analyst"):
        return "risk"
    if node_name == "Portfolio Manager":
        return "portfolio"
    return "system"


# 报告字段 → 显示标题映射，用于检测报告生成时机
REPORT_FIELD_TITLES = {
    "market_report": "市场分析报告",
    "strategy_report": "策略信号报告",
    "sentiment_report": "社交媒体情绪报告",
    "news_report": "新闻分析报告",
    "fundamentals_report": "基本面分析报告",
    "investment_plan": "研究经理投资计划",
    "trader_investment_plan": "交易员执行方案",
    "final_trade_decision": "最终交易决策",
}


# ============================================================================
# 分析执行线程
# ============================================================================

def _analysis_job_handler(job_id: str, payload: dict, cancel_event) -> dict:
    """任务队列的"分析"处理器。

    队列负责调度与取消；真正的分析逻辑仍在 :func:`run_analysis_sync`，
    两者通过 ``cancel_event`` 打通 —— 队列取消会置位该事件，
    分析循环在下一个 graph 分块边界退出。
    """
    request_data = dict(payload.get("request") or {})
    request = AnalyzeRequest(**request_data)
    # 让队列的取消事件与分析内部的取消事件联动
    if cancel_event is not None:
        _bridge_cancel_event(cancel_event)
    run_analysis_sync(request, job_id, owner=str(payload.get("owner") or ""))
    return {"success": True, "job_id": job_id}


def _bridge_cancel_event(cancel_event) -> None:
    """把队列的取消事件桥接到分析内部使用的事件。

    队列在另一个线程里取消任务时，置位它自己的 ``threading.Event``；
    分析循环读的是模块级的 ``analysis_cancel_event``。这里起一个轻量
    守护线程把前者翻译成后者，避免轮询。
    """

    def _relay() -> None:
        while not cancel_event.wait(0.25):
            continue
        analysis_cancel_event.set()

    threading.Thread(target=_relay, daemon=True, name="cancel-relay").start()


def run_analysis_sync(
    request: AnalyzeRequest, job_id: str = "", owner: str = ""
):
    global analysis_status

    from tradingagents.runtime import ANALYSIS_LOCK

    # 与定时任务互斥，避免同时读写/覆盖
    ANALYSIS_LOCK.acquire()
    try:
        try:
            mark_job_running(job_id)
        except Exception:
            pass
        analysis_status = {
            "running": True,
            "company": request.company,
            "date": request.date,
            "start_time": datetime.now().isoformat(),
            "job_id": job_id,
        }

        # 自定义图编排：合并预设与用户勾选
        pipeline_cfg = build_pipeline_config(
            analysts=request.analysts,
            enable_debate=request.enable_debate,
            enable_trader=request.enable_trader,
            enable_risk=request.enable_risk,
            preset=request.pipeline_preset,
        )
        valid_strategy_keys = {item["key"] for item in STRATEGY_META}
        strategy_keys = [
            key for key in request.strategies if key in valid_strategy_keys
        ]
        if not strategy_keys:
            strategy_keys = list(DEFAULT_STRATEGY_KEYS)

        broadcast_threadsafe({
            "type": "analysis_start",
            "data": {
                "company": request.company,
                "date": request.date,
                "pipeline": pipeline_cfg,
                "strategies": strategy_keys,
            },
        })

        config = DEFAULT_CONFIG.copy()
        config["llm_provider"] = request.llm_provider
        config["deep_think_llm"] = request.deep_think_llm
        config["quick_think_llm"] = request.quick_think_llm
        config["backend_url"] = resolve_backend_url(
            request.llm_provider, request.deep_think_llm, request.backend_url
        )
        config["pipeline"] = pipeline_cfg
        config["strategy_keys"] = strategy_keys

        # 预设模式下使用预设的辩论轮次；自定义模式下尊重用户滑块
        if request.pipeline_preset in ("lite", "standard", "deep"):
            config["max_debate_rounds"] = pipeline_cfg["max_debate_rounds"]
            config["max_risk_discuss_rounds"] = pipeline_cfg[
                "max_risk_discuss_rounds"
            ]
        else:
            config["max_debate_rounds"] = request.max_debate_rounds
            config["max_risk_discuss_rounds"] = request.max_risk_discuss_rounds

        broadcast_threadsafe({
            "type": "progress",
            "data": {
                "node": "初始化",
                "message": "正在初始化 TradingAgents 多智能体系统...",
            },
        })

        ta = TradingAgentsGraph(
            selected_analysts=pipeline_cfg["analysts"],
            debug=True,
            config=config,
        )

        init_agent_state = ta.propagator.create_initial_state(
            request.company, request.date, request.horizon
        )
        # 注意：不使用 propagator.get_graph_args() 的默认 stream_mode="values"，
        # 因为 values 模式下 chunk 不含节点名，会导致前端只能显示"处理中"。
        # 改用多模式 ["values", "updates"]：
        #   - "updates" chunk 形如 {"Market Analyst": {...update...}}，直接拿到节点名
        #   - "values"   chunk 是完整 state 快照，用于检测报告字段填充与最终汇总
        # LangGraph 多模式下，每次迭代返回元组 (mode, payload)。
        stream_args = {
            "stream_mode": ["values", "updates"],
            "config": {"recursion_limit": ta.propagator.max_recur_limit},
        }

        final_state = {}
        last_node = ""
        # 记录上一次各报告字段的值，用于检测"字段被新填充/更新"的时机
        prev_report_values = {k: "" for k in REPORT_FIELD_TITLES.keys()}
        # 缓存最近一次 updates 中的节点名，供同一轮 values chunk 复用
        # （LangGraph 多模式通常先发 updates 再发 values，但顺序不保证，故双向容错）
        pending_node_name = ""
        pending_node_update = None

        for mode, payload in ta.graph.stream(init_agent_state, **stream_args):
            # 用户点击"停止分析"后，优雅退出循环（不再处理后续 chunk）
            if analysis_cancel_event.is_set():
                broadcast_threadsafe({
                    "type": "progress",
                    "data": {"message": "用户已取消分析任务"},
                })
                break

            # ---------- 模式 1：updates —— 拿节点名 + 该节点产生的增量消息 ----------
            if mode == "updates":
                if not isinstance(payload, dict) or not payload:
                    continue
                # payload 形如 {"Market Analyst": {"messages": [...], "market_report": "..."}}
                # 通常只有一个节点，取第一个
                node_name, update = next(iter(payload.items()))
                # 跳过 LangGraph 内部元数据键（如 __interrupt__）
                if node_name.startswith("__"):
                    continue
                pending_node_name = node_name
                pending_node_update = update if isinstance(update, dict) else {}
                display_name = node_display_name(node_name)
                category = node_category(node_name)

                # 节点切换通知
                if node_name != last_node:
                    last_node = node_name
                    broadcast_threadsafe({
                        "type": "node_change",
                        "data": {
                            "node": node_name,
                            "display": display_name,
                            "category": category,
                        },
                    })

                # 检测报告字段是否在本节点被填充
                # 记录本节点刚填充的报告文本，用于后续消息去重（避免同一段报告文本
                # 既作为 report_card 显示，又作为 AIMessage 重复显示）
                freshly_filled_texts = set()
                for field, title in REPORT_FIELD_TITLES.items():
                    raw_val = pending_node_update.get(field, "")
                    cur_val = raw_val.strip() if isinstance(raw_val, str) else ""
                    prev_val = prev_report_values.get(field, "")
                    if cur_val and cur_val != prev_val:
                        broadcast_threadsafe({
                            "type": "report_ready",
                            "data": {
                                "field": field,
                                "title": title,
                                "content": cur_val,
                                "node": node_name,
                            },
                        })
                        prev_report_values[field] = cur_val
                        freshly_filled_texts.add(cur_val)

                # 处理本节点产生的消息（从 update 中取，而非 values 完整列表）
                new_messages = pending_node_update.get("messages", []) or []
                for msg in new_messages:
                    formatted = format_message_content(msg)
                    if formatted.get("is_empty"):
                        continue
                    # 跳过消息清理节点的占位 HumanMessage("Continue")
                    if formatted["role"] == "human":
                        txt = formatted.get("text", "").strip()
                        if txt == "Continue" or not txt:
                            continue
                    # 跳过 RemoveMessage（LangGraph 用来删除历史消息的标记）
                    if type(msg).__name__ == "RemoveMessage":
                        continue
                    # 去重：若本条 AI 消息的文本与刚填充的某个报告文本相同（或互为前缀），
                    # 说明同一段内容已通过 report_card 显示，跳过避免重复
                    msg_text = formatted.get("text", "").strip()
                    if msg_text and formatted["role"] == "ai":
                        for rpt_text in freshly_filled_texts:
                            # 完全相同，或报告文本是消息文本的前缀（LLM 可能在报告前加引导语）
                            # 或消息文本是报告文本的前缀
                            if (msg_text == rpt_text
                                or rpt_text.startswith(msg_text)
                                or msg_text.startswith(rpt_text)):
                                # 仅当消息文本实质内容与报告高度重合时才跳过
                                # 阈值：较短文本长度的 80%
                                min_len = min(len(msg_text), len(rpt_text))
                                if min_len > 0 and len(msg_text) >= min_len * 0.8:
                                    formatted["text"] = ""  # 标记跳过
                                    break
                        if not formatted.get("text", "").strip() and not formatted.get("tool_calls"):
                            continue
                    broadcast_threadsafe({
                        "type": "message",
                        "data": {
                            "node": node_name,
                            "display": display_name,
                            "category": category,
                            "role": formatted["role"],
                            "text": formatted.get("text", ""),
                            "tool_calls": formatted.get("tool_calls", []),
                            "tool_name": formatted.get("tool_name", ""),
                            "is_simulated": formatted.get("is_simulated", False),
                            "msg_type": type(msg).__name__,
                        },
                    })
                continue

            # ---------- 模式 2：values —— 拿完整 state 快照 ----------
            if mode == "values":
                if not isinstance(payload, dict):
                    continue
                # 更新最终状态（最后一次 values 即为最终 state）
                final_state = payload
                # 补充检测：有些节点（如 Research Manager / Portfolio Manager）可能在
                # updates chunk 里没有 report 字段（值未变时），但 values 里有最新值
                for field, title in REPORT_FIELD_TITLES.items():
                    raw = payload.get(field, "")
                    cur_val = raw.strip() if isinstance(raw, str) else ""
                    prev_val = prev_report_values.get(field, "")
                    if cur_val and cur_val != prev_val:
                        broadcast_threadsafe({
                            "type": "report_ready",
                            "data": {
                                "field": field,
                                "title": title,
                                "content": cur_val,
                                "node": pending_node_name or field,
                            },
                        })
                        prev_report_values[field] = cur_val
                continue

        decision = ta.process_signal(
            final_state.get("final_trade_decision", "无法确定")
        )

        from tradingagents.services.decision_plan import (
            build_decision_plan,
            compare_decisions,
            format_decision_plan,
        )
        from tradingagents.services.decision_schema import (
            format_structured_decision,
            parse_structured_decision,
        )
        from tradingagents.services.fast_preview import preliminary_decision

        structured_decision = final_state.get("decision_structured") or (
            parse_structured_decision(
                final_state.get("final_trade_decision", ""), decision
            )
        )

        account = f"默认账户@{owner}" if owner else "默认账户"
        strategy_result = final_state.get("strategy_result")
        decision_plan = build_decision_plan(strategy_result, decision)

        # 规则初判与 LLM 最终决策的差异（两者来源独立，分歧值得显式呈现）
        preliminary = preliminary_decision(
            (strategy_result or {}).get("summary") or {}
        )
        preliminary_plan = build_decision_plan(strategy_result, preliminary)
        decision_diff = compare_decisions(
            preliminary, decision, preliminary_plan, decision_plan
        )

        # 组合级校验：把决策卡放到当前账户语境下重新评估，
        # 得到"真正可执行"的仓位（此前建议仓位与组合完全脱节）。
        portfolio_evaluation = {}
        try:
            from tradingagents.services.portfolio import (
                evaluate_plan_for_account,
            )

            portfolio_evaluation = evaluate_plan_for_account(
                plan=decision_plan,
                summary=(strategy_result or {}).get("summary") or {},
                account=account,
                ticker=request.company,
                owner=owner,
            )
        except ApiError:
            raise
        except Exception as exc:
            portfolio_evaluation = {"error": str(exc)}

        # 持仓建议：仅当账户持有该标的时生成（分析回答"买不买"，
        # 持仓者真正关心的是"卖不卖"）。
        position_advice = None
        try:
            from tradingagents.services.position_advisor import (
                advice_for_analysis,
            )

            position_advice = advice_for_analysis(
                request.company, decision_plan, account=account, owner=owner
            )
        except Exception:
            logger.debug("持仓建议生成失败", exc_info=True)

        # 完整 reports 字段：保持与 AgentState 字段名一致，前端按 key 渲染对应卡片
        # 注意：investment_plan 是研究经理的推荐，trader_investment_plan 是交易员的执行方案
        reports = {
            "market_report": final_state.get("market_report", ""),
            "strategy_report": final_state.get("strategy_report", ""),
            "sentiment_report": final_state.get("sentiment_report", ""),
            "news_report": final_state.get("news_report", ""),
            "fundamentals_report": final_state.get("fundamentals_report", ""),
            "investment_plan": final_state.get("investment_plan", ""),
            "trader_investment_plan": final_state.get("trader_investment_plan", ""),
            "final_trade_decision": final_state.get("final_trade_decision", ""),
            "decision_plan": format_decision_plan(decision_plan),
            "decision_structured": format_structured_decision(structured_decision),
        }
        if position_advice:
            reports["position_advice"] = position_advice["markdown"]

        report_id = None
        if not analysis_cancel_event.is_set():
            try:
                report_id = save_report(
                    ticker=request.company,
                    trade_date=request.date,
                    decision=decision,
                    decision_text=final_state.get("final_trade_decision", ""),
                    reports=reports,
                    source="webui",
                    owner=owner,
                    meta={
                        "llm_provider": request.llm_provider,
                        "deep_think_llm": request.deep_think_llm,
                        "quick_think_llm": request.quick_think_llm,
                        "horizon": request.horizon,
                        "pipeline_preset": request.pipeline_preset,
                        "pipeline": pipeline_cfg,
                        "strategies": strategy_keys,
                        "decision_plan": decision_plan,
                        "decision_diff": decision_diff,
                        "decision_structured": structured_decision,
                        "portfolio_evaluation": portfolio_evaluation,
                        "position_advice": position_advice,
                    },
                )
            except ApiError:
                raise
            except Exception as exc:
                broadcast_threadsafe({
                    "type": "progress",
                    "data": {"message": f"报告持久化失败: {exc}"},
                })

        cancelled = analysis_cancel_event.is_set()
        complete_data = {
            "decision": decision,
            "decision_text": final_state.get("final_trade_decision", ""),
            "reports": reports,
            "company": request.company,
            "date": request.date,
            "report_id": report_id,
            "pipeline": pipeline_cfg,
            "strategies": strategy_keys,
            "decision_plan": decision_plan,
            "decision_diff": decision_diff,
            "decision_structured": structured_decision,
            "portfolio_evaluation": portfolio_evaluation,
            "position_advice": position_advice,
            "cancelled": cancelled,
        }
        try:
            finish_job(
                job_id,
                complete_data,
                status="cancelled" if cancelled else "completed",
            )
        except Exception:
            pass
        broadcast_threadsafe({
            "type": "analysis_complete",
            "data": complete_data,
        })

    except Exception as e:
        err_text = str(e)
        config_hint = locals().get("config", {}) or {}
        backend_hint = config_hint.get("backend_url") or "未配置 Base URL"
        lower = err_text.lower()
        if "connection error" in lower or type(e).__name__ in (
            "APIConnectionError",
            "ConnectError",
        ):
            error_msg = (
                f"无法连接模型服务（{backend_hint}）。请检查 Base URL、网络或代理。"
                f"原始错误：{err_text}"
            )
        elif "authentication" in lower or "api key" in lower or "401" in lower:
            error_msg = (
                f"模型鉴权失败（{backend_hint}）。请检查 .env 中的 API Key。"
                f"原始错误：{err_text}"
            )
        else:
            error_msg = f"{type(e).__name__}: {err_text}"
        traceback_str = traceback.format_exc()
        try:
            fail_job(job_id, error_msg)
        except Exception:
            pass
        broadcast_threadsafe({
            "type": "error",
            "data": {
                "message": error_msg,
                "traceback": traceback_str,
            },
        })
    finally:
        was_cancelled = analysis_cancel_event.is_set()
        analysis_status["running"] = False
        analysis_status["cancelled"] = was_cancelled
        # 清除取消标志，为下次分析做准备
        analysis_cancel_event.clear()
        broadcast_threadsafe({
            "type": "status",
            "data": analysis_status,
        })
        ANALYSIS_LOCK.release()


# ============================================================================
# 多用户账户归属
# ============================================================================

def _is_admin_user(user: dict[str, Any] | None) -> bool:
    if not load_config().multi_user:
        return True
    return (user or {}).get("role") == "admin"


def _default_account_for(user: dict[str, Any] | None) -> str:
    if not load_config().multi_user or _is_admin_user(user):
        return "默认账户"
    username = (user or {}).get("username") or "user"
    return f"默认账户@{username}"


def _resolve_account(
    account: str | None, user: dict[str, Any] | None
) -> str:
    """解析并校验账户归属；非管理员只能访问自己的账户。"""
    name = (account or "").strip() or _default_account_for(user)
    if not load_config().multi_user or _is_admin_user(user):
        return name
    row = get_account(name)
    username = (user or {}).get("username") or ""
    if row and str(row.get("owner") or "") != username:
        raise ForbiddenError("无权访问该账户")
    return name


def _resolve_paper_account(
    account: str | None, user: dict[str, Any] | None
) -> str:
    if not load_config().multi_user or _is_admin_user(user):
        return (account or "默认模拟盘").strip() or "默认模拟盘"
    username = (user or {}).get("username") or "user"
    name = (account or "").strip() or f"默认模拟盘@{username}"
    from tradingagents.storage import get_paper_owner

    owner = get_paper_owner(name)
    if owner and owner != username:
        raise ForbiddenError("无权访问该模拟盘账户")
    return name


# ============================================================================
# 模拟盘辅助：状态按账户存取
# ============================================================================

def _paper_account_name(
    account: str | None, user: dict[str, Any] | None = None
) -> str:
    if user is not None:
        return _resolve_paper_account(account, user)
    return (account or "默认模拟盘").strip() or "默认模拟盘"


def _load_paper_broker(
    account: str | None, user: dict[str, Any] | None = None
):
    from tradingagents.services.paper_trading import PaperBroker

    name = _paper_account_name(account, user)
    state = load_paper_state(name)
    broker = PaperBroker.from_state(state) if state else PaperBroker()
    # WebUI 的模拟盘是“实时”账户：历史回放不得让今天提交的委托按过去价格成交。
    broker.enforce_submission_date = True
    return name, broker


def _save_paper_broker(name: str, broker, owner: str = "") -> None:
    save_paper_state(name, broker.to_state(), owner=owner)


def _paper_payload(name: str, broker) -> dict[str, Any]:
    return {
        "success": True,
        "paper_account": name,
        **broker.report(last_orders=100),
    }


# ============================================================================
# 认证 / 用户 / 审计
# ============================================================================

@auth_router.get("/auth/config", tags=["认证"], summary="查询登录模式")
async def api_auth_config():
    config = load_config()
    from webui.backend.users import ROLES

    return {
        "success": True,
        "multi_user": bool(config.multi_user),
        "api_key_enabled": bool(config.api_key),
        "session_days": int(config.session_days),
        "roles": list(ROLES),
    }


@auth_router.post("/auth/login", tags=["认证"], summary="用户登录")
async def api_auth_login(request: Request, payload: LoginRequest):
    from webui.backend.users import audit_log, authenticate, create_session

    user = await run_in_threadpool(
        authenticate, payload.username, payload.password
    )
    config = load_config()
    ip = request.client.host if request.client else ""
    if not user:
        audit_log(
            username=payload.username,
            role=None,
            action="login_failed",
            method="POST",
            path=request.url.path,
            status=401,
            ip=ip,
            request_id=getattr(request.state, "request_id", ""),
        )
        raise AuthError("用户名或密码错误")
    token = create_session(
        user["username"],
        ip=ip,
        user_agent=request.headers.get("user-agent", ""),
    )
    audit_log(
        username=user["username"],
        role=user["role"],
        action="login",
        method="POST",
        path=request.url.path,
        status=200,
        ip=ip,
        request_id=getattr(request.state, "request_id", ""),
    )
    return {"success": True, "token": token, "user": user, "session_days": config.session_days}


@api_router.get("/auth/me", tags=["认证"], summary="当前用户")
async def api_auth_me(user: dict = Depends(current_user)):
    return {"success": True, "user": user}


@api_router.post("/auth/logout", tags=["认证"], summary="退出登录")
async def api_auth_logout(request: Request, user: dict = Depends(current_user)):
    from webui.backend.security import extract_api_key
    from webui.backend.users import audit_log, revoke_session

    token = extract_api_key(request)
    revoked = revoke_session(token) if token else False
    audit_log(
        username=user.get("username"),
        role=user.get("role"),
        action="logout",
        method="POST",
        path=request.url.path,
        status=200,
        ip=request.client.host if request.client else "",
        request_id=getattr(request.state, "request_id", ""),
    )
    return {"success": True, "revoked": bool(revoked)}


@api_router.post("/auth/change-password", tags=["认证"], summary="修改密码")
async def api_auth_change_password(
    request: Request,
    payload: ChangePasswordRequest,
    user: dict = Depends(current_user),
):
    from webui.backend.users import (
        audit_log,
        authenticate,
        revoke_user_sessions,
        update_user,
    )

    username = user.get("username")
    if username in ("local", "api-key") or not authenticate(
        username, payload.old_password
    ):
        raise AuthError("原密码不正确")
    update_user(username, password=payload.new_password)
    revoke_user_sessions(username)
    audit_log(
        username=username,
        role=user.get("role"),
        action="change_password",
        method="POST",
        path=request.url.path,
        status=200,
        ip=request.client.host if request.client else "",
        request_id=getattr(request.state, "request_id", ""),
    )
    return {"success": True, "message": "密码已修改，请重新登录", "relogin": True}


@api_router.post("/auth/risk-ack", tags=["认证"], summary="确认风险揭示")
async def api_auth_risk_ack(user: dict = Depends(current_user)):
    from webui.backend.users import ack_risk

    updated = ack_risk(user.get("username"))
    return {"success": bool(updated), "user": updated}


@api_router.get(
    "/users",
    tags=["认证"],
    summary="用户列表（管理员）",
    dependencies=[Depends(require_role("admin"))],
)
async def api_list_users():
    from webui.backend.users import list_users

    return {"success": True, "items": await run_in_threadpool(list_users)}


@api_router.post(
    "/users",
    tags=["认证"],
    summary="创建用户（管理员）",
    dependencies=[Depends(require_role("admin"))],
)
async def api_create_user(payload: UserCreateRequest):
    from webui.backend.users import create_user

    try:
        user = await run_in_threadpool(
            create_user,
            payload.username,
            payload.password,
            role=payload.role,
            display_name=payload.display_name,
        )
        return {"success": True, "user": user}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc), code="bad_request")


@api_router.patch(
    "/users/{username}",
    tags=["认证"],
    summary="修改用户（管理员）",
    dependencies=[Depends(require_role("admin"))],
)
async def api_update_user(username: str, payload: UserUpdateRequest):
    from webui.backend.users import update_user

    try:
        user = await run_in_threadpool(
            update_user,
            username,
            role=payload.role,
            enabled=payload.enabled,
            display_name=payload.display_name,
            password=payload.password,
        )
        if not user:
            return error_payload("用户不存在", code="not_found")
        return {"success": True, "user": user}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc), code="bad_request")


@api_router.delete(
    "/users/{username}",
    tags=["认证"],
    summary="删除用户（管理员）",
    dependencies=[Depends(require_role("admin"))],
)
async def api_delete_user(username: str, user: dict = Depends(current_user)):
    from webui.backend.users import delete_user

    if user.get("username") == username:
        return error_payload("不能删除当前登录用户", code="bad_request")
    try:
        removed = await run_in_threadpool(delete_user, username)
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc), code="bad_request")
    return {"success": bool(removed), "message": "已删除" if removed else "用户不存在"}


@api_router.get(
    "/audit",
    tags=["认证"],
    summary="审计日志（管理员）",
    dependencies=[Depends(require_role("admin"))],
)
async def api_audit_logs(limit: int = 100, username: str | None = None):
    from webui.backend.users import list_audit_logs

    items = await run_in_threadpool(
        list_audit_logs, max(1, min(int(limit), 1000)), username=username
    )
    return {"success": True, "items": items}


# ============================================================================
# REST API 端点
# ============================================================================

@api_router.post("/analyze", tags=["分析"], summary="启动深度分析任务")
async def start_analysis(
    request: AnalyzeRequest,
    _user: dict = Depends(current_user),
    _risk: None = Depends(require_risk_ack),
):
    # 允许用户在不重启服务的情况下新建/修改 .env 中缺失的密钥
    load_dotenv()
    try:
        _validate_backend_url(request.backend_url)
    except ValueError as exc:
        return error_payload(safe_exception_message(exc), code="bad_request")

    # 先做参数校验，再检查队列容量 —— 无效请求不应该先占用/探测队列。
    date_ok, date_msg, adjusted_date = validate_analysis_date(
        request.company, request.date
    )
    if not date_ok:
        return {
            **error_payload(date_msg, code="bad_request"),
            "suggested_date": adjusted_date,
        }

    # 仅 A 股模式：非 A 股标的在提交任务前就拒绝，
    # 避免任务进入队列后再一路打到美股数据源。
    if DEFAULT_CONFIG.get("a_share_only", True):
        from tradingagents.dataflows.ashare_rules import (
            contains_cjk,
            is_a_share_code,
        )

        company = str(request.company).strip()
        if not (
            is_a_share_code(company)
            or contains_cjk(company)
            or (company.isdigit() and len(company) <= 6)
        ):
            return error_payload(
                f"当前为仅 A 股模式，已拒绝非 A 股标的：{company}"
                "（如需分析美股/港股，请设置 A_SHARE_ONLY=0）",
                code="bad_request",
            )

    # 并发控制改为**有界队列**：
    # 此前这里用一个进程级互斥锁把整个系统串行化，且 running 标志是在
    # 取锁之后才置位 —— N 个并发请求会全部看到"空闲"并全部返回"已启动"，
    # 然后 N-1 个线程无限期阻塞在锁上。现在改为有界队列：超出容量才拒绝。
    queue = ensure_job_queue(
        worker_count=ANALYSIS_WORKERS,
        handlers={"analysis": _analysis_job_handler},
    )
    if queue.active_count() + queue.pending_count() >= MAX_PENDING_ANALYSES:
        return {
            **error_payload(
                f"分析队列已满（运行 {queue.active_count()} 个，"
                f"排队 {queue.pending_count()} 个），请等待完成后再试",
                code="conflict",
            ),
            "job_id": analysis_status.get("job_id", ""),
        }

    # 启动前校验 API Key，避免把底层鉴权异常直接抛到前端
    from tradingagents.llm_clients.auth import ensure_provider_credentials

    backend_url = resolve_backend_url(
        request.llm_provider, request.deep_think_llm, request.backend_url
    )
    try:
        ensure_provider_credentials(
            request.llm_provider, backend_url, None
        )
    except RuntimeError as exc:
        return error_payload(safe_exception_message(exc))

    owner = ""
    if load_config().multi_user and not _is_admin_user(_user):
        owner = (_user or {}).get("username") or ""
    job_id = queue.submit(
        payload={"request": request.model_dump(), "owner": owner},
        kind="analysis",
        company=request.company,
        trade_date=request.date,
        meta={
            "pipeline_preset": request.pipeline_preset,
            "analysts": request.analysts,
            "strategies": request.strategies,
            "llm_provider": request.llm_provider,
        },
    )

    return {
        "success": True,
        "message": f"分析任务已排队: {request.company} ({request.date})",
        "job_id": job_id,
        "queue_position": queue.pending_count(),
    }


@api_router.get("/status", tags=["分析"], summary="查询当前分析任务状态")
async def get_status():
    status = dict(analysis_status)
    queue = get_job_queue()
    if queue is not None:
        status["queue_active"] = queue.active_count()
        status["queue_pending"] = queue.pending_count()
        status["queue_workers"] = queue.worker_count
    return status


@api_router.post(
    "/stop",
    tags=["分析"],
    summary="请求停止当前分析任务",
    response_model=SuccessMessageResponse,
)
async def stop_analysis():
    """
    请求停止当前正在运行的分析任务。
    设置取消事件标志，run_analysis_sync 在下一次 graph.stream 迭代时检测到后优雅退出。
    注意：这是协作式取消，不能立即中断正在进行的 LLM 调用或工具调用，
    只能在节点之间切换时生效。
    """
    if not analysis_status["running"]:
        return error_payload("当前没有正在运行的分析任务")
    analysis_cancel_event.set()
    return {"success": True, "message": "已发送停止信号，任务将在当前节点完成后终止"}


@api_router.get(
    "/jobs",
    tags=["任务"],
    summary="查询分析任务列表",
    response_model=ItemListResponse,
)
async def api_list_jobs(limit: int = 20, offset: int = 0):
    """最近的分析任务列表（不含完整报告，结果请查 /result）。"""
    return {
        "success": True,
        "items": await run_in_threadpool(list_jobs, limit, offset),
    }


@api_router.get("/jobs/{job_id}", tags=["任务"], summary="查询分析任务详情")
async def api_get_job(job_id: str):
    job = await run_in_threadpool(get_job, job_id, False)
    if not job:
        return error_payload("任务不存在", code="not_found")
    return {"success": True, "job": job}


@api_router.get("/jobs/{job_id}/result", tags=["任务"], summary="查询分析任务结果")
async def api_get_job_result(job_id: str):
    job = await run_in_threadpool(get_job, job_id, True)
    if not job:
        return error_payload("任务不存在", code="not_found")
    if job["status"] in ("completed", "cancelled") and job.get("result"):
        return {
            "success": True,
            "job_id": job["job_id"],
            "status": job["status"],
            "result": job["result"],
        }
    if job["status"] == "failed":
        return {
            **error_payload(job.get("error") or "任务失败"),
            "job_id": job["job_id"],
            "status": job["status"],
        }
    return {
        **error_payload("任务尚未完成"),
        "job_id": job["job_id"],
        "status": job["status"],
    }


@api_router.post("/jobs/{job_id}/cancel", tags=["任务"], summary="取消分析任务")
async def api_cancel_job(job_id: str):
    job = await run_in_threadpool(get_job, job_id, False)
    if not job:
        return error_payload("任务不存在", code="not_found")
    if job["status"] not in ("queued", "running"):
        return {
            **error_payload("任务已结束，无法取消"),
            "status": job["status"],
        }
    if analysis_status.get("job_id") != job_id:
        return {
            **error_payload("该任务尚未成为当前运行任务，暂不支持取消"),
            "status": job["status"],
        }
    analysis_cancel_event.set()
    return {
        "success": True,
        "job_id": job_id,
        "message": "已发送停止信号，任务将在当前节点完成后终止",
    }


@api_router.delete(
    "/jobs/{job_id}",
    tags=["任务"],
    summary="删除分析任务",
    response_model=SuccessMessageResponse,
)
async def api_delete_job(job_id: str):
    job = await run_in_threadpool(get_job, job_id, False)
    if not job:
        return error_payload("任务不存在", code="not_found")
    if job["status"] == "running":
        return error_payload("运行中的任务不能删除")
    deleted = await run_in_threadpool(delete_job, job_id)
    if not deleted:
        return error_payload("任务不存在", code="not_found")
    return {"success": True, "message": "已删除"}


# ============================================================================
# 自选股 / 意图解析 / 回测 / K 线 / 定时任务 API
# ============================================================================

@api_router.get(
    "/watchlist",
    tags=["自选"],
    summary="查询自选股列表",
    response_model=ItemListResponse,
)
async def api_list_watchlist(user: dict = Depends(current_user)):
    owner = None if _is_admin_user(user) else (user or {}).get("username") or ""
    return {"success": True, "items": list_watchlist(owner)}


@api_router.post(
    "/watchlist",
    tags=["自选"],
    summary="新增自选股",
    response_model=WatchlistItemResponse,
)
async def api_add_watch(
    request: WatchRequest, user: dict = Depends(current_user)
):
    owner = "" if _is_admin_user(user) else (user or {}).get("username") or ""
    item = add_watch(request.ticker, request.name, owner)
    return {"success": True, "item": item}


@api_router.delete(
    "/watchlist/{ticker}",
    tags=["自选"],
    summary="删除自选股",
    response_model=SuccessMessageResponse,
)
async def api_remove_watch(
    ticker: str, user: dict = Depends(current_user)
):
    owner = "" if _is_admin_user(user) else (user or {}).get("username") or ""
    removed = remove_watch(ticker, owner)
    if not removed:
        return error_payload("自选股不存在", code="not_found")
    return {"success": True, "message": "已删除"}


@api_router.post("/intent", tags=["分析"], summary="解析自然语言选股意图")
async def api_parse_intent(request: IntentRequest):
    return {"success": True, "intent": parse_intent(request.query)}


def _resolve_stock_name(ticker: str) -> str:
    """解析 A 股标的真实名称（用于 ST 涨跌停判定），失败返回空串。"""
    try:
        from tradingagents.dataflows.akshare_adapter import resolve_name_by_code

        return resolve_name_by_code(ticker)
    except Exception:
        return ""


@api_router.post("/backtest", tags=["策略"], summary="运行经典策略回测")
async def api_backtest(request: BacktestRequest):
    try:
        result = await run_in_threadpool(
            run_backtest,
            ticker=request.ticker,
            start_date=request.start_date,
            end_date=request.end_date,
            strategy=request.strategy,
            fast=request.fast,
            slow=request.slow,
            initial_cash=request.initial_cash,
            commission_rate=request.commission_rate,
            stamp_duty_rate=request.stamp_duty_rate,
            slippage_rate=request.slippage_rate,
            # 名称决定 ST/*ST 的 5% 涨跌停，不传会让 ST 股按主板 10% 计算
            stock_name=_resolve_stock_name(request.ticker),
        )
        return {"success": True, "result": result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/market/kline", tags=["市场"], summary="查询 K 线数据")
async def api_kline(
    ticker: str,
    range: str = "6m",
    limit: int | None = None,
    end_date: str | None = None,
):
    """返回 K 线数据。

    range 支持：1m / 3m / 6m / 1y / 3y / 5y / all。
    all 会尽量拉取约 20 年历史，前端可缩放/平移查看。
    end_date 用于历史分析：K 线截止到分析日，避免展示未来行情。
    """
    try:
        from datetime import timedelta

        range_days = {
            "1m": 31,
            "3m": 92,
            "6m": 183,
            "1y": 365,
            "3y": 1095,
            "5y": 1825,
            "all": 7300,
        }
        key = (range or "6m").lower()
        days = range_days.get(key, 183)
        end = datetime.now()
        if end_date:
            try:
                parsed = datetime.strptime(str(end_date)[:10], "%Y-%m-%d")
                if parsed <= end:
                    end = parsed
            except ValueError:
                pass
        start = end - timedelta(days=days)
        # 行情抓取为阻塞 IO，放入线程池，避免阻塞 FastAPI 事件循环
        frame = await run_in_threadpool(
            _load_prices,
            ticker,
            start.strftime("%Y-%m-%d"),
            end.strftime("%Y-%m-%d"),
        )
        if limit:
            frame = frame.tail(max(20, min(int(limit), 6000)))

        items = []
        for _, row in frame.iterrows():
            items.append(
                {
                    "date": row["Date"].strftime("%Y-%m-%d"),
                    "open": float(row.get("Open", row["Close"])),
                    "high": float(row.get("High", row["Close"])),
                    "low": float(row.get("Low", row["Close"])),
                    "close": float(row["Close"]),
                    "volume": float(row.get("Volume", 0) or 0),
                }
            )
        return {
            "success": True,
            "ticker": ticker,
            "range": key,
            "bars": len(items),
            "items": items,
        }
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


def _to_float(value) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _parse_flow_csv(text: str) -> list[dict] | None:
    """解析资金流工具返回的“注释头 + CSV”文本。"""
    if not isinstance(text, str) or not text.startswith("#"):
        return None
    parts = text.split("\n\n", 1)
    if len(parts) != 2:
        return None
    return list(csv.DictReader(io.StringIO(parts[1])))


def _parse_strategy_keys(value: str | None) -> list[str] | None:
    if not value:
        return None
    keys = [item.strip() for item in value.split(",") if item.strip()]
    return keys or None


def _parse_tickers(value: str | None) -> list[str] | None:
    if not value:
        return None
    tickers = [item.strip() for item in value.split(",") if item.strip()]
    return tickers or None


@api_router.get("/preview", tags=["策略"], summary="规则化快速预览（不调用 LLM）")
async def api_fast_preview(
    ticker: str,
    end_date: str | None = None,
    strategies: str | None = None,
):
    """规则化快速预览（不调用 LLM），用于深度分析期间先给用户一个初判。"""
    try:
        from tradingagents.services.fast_preview import build_fast_preview

        return await run_in_threadpool(
            build_fast_preview,
            ticker,
            end_date,
            _parse_strategy_keys(strategies),
        )
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get(
    "/strategies/meta",
    tags=["策略"],
    summary="查询策略元数据",
    response_model=StrategyMetaResponse,
)
async def api_strategies_meta():
    """返回可选策略列表与默认核心组合。"""
    return {
        "success": True,
        "strategies": STRATEGY_META,
        "default": list(DEFAULT_STRATEGY_KEYS),
    }


@api_router.get("/strategy-backtest", tags=["策略"], summary="对选中策略做信号有效性回测")
async def api_strategy_backtest(
    ticker: str,
    end_date: str | None = None,
    strategies: str | None = None,
    days: int | None = None,
):
    """对选中的策略做信号有效性回测。

    ``days`` 缺省为 None —— 服务端会使用与实盘扫描完全一致的 500 个
    自然日窗口。此前默认 250 根 bar，会把样本外有效样本量压到门槛
    以下，导致所有策略都被判为"样本不足"。
    """
    try:
        from tradingagents.services.strategy_backtest import (
            run_strategy_backtest,
        )

        return await run_in_threadpool(
            run_strategy_backtest,
            ticker,
            end_date,
            _parse_strategy_keys(strategies),
            days,
        )
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/strategies", tags=["策略"], summary="运行经典策略扫描")
async def api_strategies(
    ticker: str,
    end_date: str | None = None,
    strategies: str | None = None,
):
    """运行选中的经典策略扫描。"""
    try:
        from tradingagents.strategies.scanner import scan_all_strategies

        return await run_in_threadpool(
            scan_all_strategies,
            ticker,
            end_date,
            _parse_strategy_keys(strategies),
        )
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/strategies/watchlist", tags=["策略"], summary="扫描自选股的策略共识")
async def api_strategies_watchlist(
    end_date: str | None = None,
    strategies: str | None = None,
):
    """扫描自选股的策略共识。"""
    try:
        from tradingagents.strategies.scanner import scan_watchlist

        items = list_watchlist()
        tickers = [item["ticker"] for item in items]
        results = await run_in_threadpool(
            scan_watchlist,
            tickers,
            end_date,
            _parse_strategy_keys(strategies),
        )
        return {"success": True, "items": results}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/strategy-alerts", tags=["策略"], summary="查询策略触发预警")
async def api_strategy_alerts(
    end_date: str | None = None,
    strategies: str | None = None,
    tickers: str | None = None,
    only_triggered: bool = True,
):
    """扫描自选股并返回触发型策略预警。"""
    try:
        from tradingagents.services.strategy_alerts import (
            build_strategy_alerts,
        )

        return await run_in_threadpool(
            build_strategy_alerts,
            _parse_tickers(tickers),
            end_date,
            _parse_strategy_keys(strategies),
            only_triggered,
        )
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.post("/strategy-alerts/notify", tags=["策略"], summary="推送策略预警到 Webhook")
async def api_strategy_alerts_notify(request: StrategyAlertNotifyRequest):
    """扫描自选股并推送到已配置的 Webhook 渠道。"""
    try:
        from tradingagents.services.strategy_alerts import (
            send_strategy_alerts,
        )

        return await run_in_threadpool(
            send_strategy_alerts,
            request.tickers,
            request.end_date,
            request.strategies,
            request.only_triggered,
            request.notify_empty,
        )
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/market/review", tags=["市场"], summary="查询市场复盘快照")
async def api_market_review(date: str | None = None):
    """返回指数、市场情绪、行业热度和涨停分布的复盘快照。"""
    try:
        from tradingagents.services.market_review import build_market_review

        return await run_in_threadpool(build_market_review, date)
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/daily-briefing", tags=["报告"], summary="查询每日盘后简报")
async def api_daily_briefing(date: str | None = None):
    """返回市场复盘 + 自选股预警合并后的盘后简报。"""
    try:
        from tradingagents.services.daily_briefing import build_daily_briefing

        return await run_in_threadpool(build_daily_briefing, date)
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.post("/daily-briefing/notify", tags=["报告"], summary="推送每日盘后简报")
async def api_daily_briefing_notify(request: DailyBriefingNotifyRequest):
    """生成并推送每日盘后简报。"""
    try:
        from tradingagents.services.daily_briefing import send_daily_briefing

        return await run_in_threadpool(
            send_daily_briefing, request.date, "webui"
        )
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.post("/daily-briefing/archive", tags=["报告"], summary="归档每日盘后简报")
async def api_daily_briefing_archive(request: DailyBriefingNotifyRequest):
    """生成并归档每日盘后简报。"""
    try:
        from tradingagents.services.daily_briefing import (
            archive_daily_briefing,
        )

        return await run_in_threadpool(
            archive_daily_briefing, request.date, "webui"
        )
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get(
    "/briefings",
    tags=["报告"],
    summary="查询盘后简报列表",
    response_model=ItemListResponse,
)
async def api_list_briefings(limit: int = 50, offset: int = 0):
    return {
        "success": True,
        "items": await run_in_threadpool(list_briefings, limit, offset),
    }


@api_router.get("/briefings/{briefing_id}", tags=["报告"], summary="查询盘后简报详情")
async def api_get_briefing(briefing_id: int):
    item = await run_in_threadpool(get_briefing, briefing_id)
    if not item:
        return error_payload("简报不存在", code="not_found")
    return {"success": True, "briefing": item}


@api_router.delete(
    "/briefings/{briefing_id}",
    tags=["报告"],
    summary="删除盘后简报",
    response_model=SuccessMessageResponse,
)
async def api_delete_briefing(briefing_id: int):
    removed = await run_in_threadpool(delete_briefing, briefing_id)
    if not removed:
        return error_payload("简报不存在", code="not_found")
    return {"success": True, "message": "已删除"}


@api_router.get("/market/sectors", tags=["市场"], summary="查询行业板块列表")
async def api_market_sectors():
    """返回同花顺行业列表，供板块历史查询使用。"""
    try:
        from tradingagents.services.sector_history import list_sector_names

        items = await run_in_threadpool(list_sector_names)
        return {"success": True, "items": items}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/market/sector-history", tags=["市场"], summary="查询行业近期走势")
async def api_sector_history(name: str, days: int = 60):
    """返回单个行业的近期走势和 1/5/20 日涨跌幅。"""
    try:
        from tradingagents.services.sector_history import get_sector_history

        return await run_in_threadpool(get_sector_history, name, days)
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/market/stock-search", tags=["市场"], summary="搜索 A 股标的")
async def api_stock_search(q: str = "", limit: int = 20):
    """按代码或名称搜索 A 股，供前端自动补全使用。"""
    try:
        from tradingagents.services.market_search import search_stocks

        return await run_in_threadpool(search_stocks, q, limit)
    except ApiError:
        raise
    except Exception as exc:
        return {**error_payload(safe_exception_message(exc)), "items": []}


@api_router.get("/market/hot-stocks", tags=["市场"], summary="查询涨停池热门股")
async def api_hot_stocks(limit: int = 30, date: str | None = None):
    """返回涨停池热门股。"""
    try:
        from tradingagents.services.market_search import get_hot_stocks

        return await run_in_threadpool(get_hot_stocks, limit, date)
    except ApiError:
        raise
    except Exception as exc:
        return {**error_payload(safe_exception_message(exc)), "items": []}


@api_router.post("/market/review/notify", tags=["市场"], summary="推送市场复盘到 Webhook")
async def api_market_review_notify(request: MarketReviewNotifyRequest):
    """生成并推送市场复盘到已配置的 Webhook 渠道。"""
    try:
        from tradingagents.services.market_review import send_market_review

        return await run_in_threadpool(send_market_review, request.date)
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/market/capital-flow", tags=["市场"], summary="查询个股主力资金流向")
async def api_capital_flow(ticker: str, days: int = 60):
    """返回个股主力资金流向序列，供前端绘制柱状图。"""
    try:
        from tradingagents.dataflows.akshare_adapter import (
            get_akshare_capital_flow,
        )

        days = max(5, min(int(days), 120))
        text = await run_in_threadpool(
            get_akshare_capital_flow, ticker, None, days
        )
        rows = _parse_flow_csv(text)
        if not rows:
            return error_payload(text)

        main_col = next(
            (k for k in rows[0] if "主力净流入" in k and "净额" in k), None
        )
        pct_col = next(
            (k for k in rows[0] if "主力净流入" in k and "净占比" in k), None
        )
        items = []
        for row in reversed(rows):  # 工具返回新→旧，图表需要旧→新
            items.append(
                {
                    "date": row.get("日期", ""),
                    "main_net": _to_float(row.get(main_col)) if main_col else 0.0,
                    "main_pct": _to_float(row.get(pct_col)) if pct_col else 0.0,
                    "close": _to_float(row.get("收盘价")),
                    "change_pct": _to_float(row.get("涨跌幅")),
                }
            )
        return {"success": True, "ticker": ticker, "items": items}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/market/northbound", tags=["市场"], summary="查询北向资金净流入")
async def api_northbound(days: int = 10):
    """返回北向资金近 N 个交易日净流入序列。"""
    try:
        from tradingagents.dataflows.akshare_adapter import (
            get_akshare_northbound_flow,
        )

        days = max(1, min(int(days), 60))
        text = await run_in_threadpool(
            get_akshare_northbound_flow, None, days
        )
        rows = _parse_flow_csv(text)
        if not rows:
            return error_payload(text)
        net_col = next(
            (
                k
                for k in rows[0]
                if "当日成交净买额" in k or "当日资金流入" in k
            ),
            None,
        )
        items = []
        for row in reversed(rows):
            items.append(
                {
                    "date": row.get("日期", ""),
                    "net": _to_float(row.get(net_col)) if net_col else 0.0,
                }
            )
        return {"success": True, "items": items}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.post("/test-llm", tags=["分析"], summary="测试模型端点连通性")
async def api_test_llm(request: LLMTestRequest):
    """用一次最小请求测试模型端点连通性（不返回密钥）。"""
    load_dotenv()
    try:
        _validate_backend_url(request.backend_url)
    except ValueError as exc:
        return error_payload(safe_exception_message(exc), code="bad_request")
    from tradingagents.llm_clients.auth import ensure_provider_credentials
    from tradingagents.llm_clients.factory import create_llm_client

    backend = resolve_backend_url(
        request.llm_provider, request.model, request.backend_url
    )
    try:
        ensure_provider_credentials(request.llm_provider, backend)
        client = create_llm_client(
            request.llm_provider, request.model, base_url=backend
        )
        llm = client.get_llm()
        response = await run_in_threadpool(llm.invoke, "请只回复 OK")
        text = str(getattr(response, "content", response))[:200]
        return {
            "success": True,
            "backend_url": backend,
            "model": request.model,
            "message": f"连接成功：{text}",
        }
    except ApiError:
        raise
    except Exception as exc:
        return {
            **error_payload(safe_exception_message(exc)),
            "backend_url": backend,
            "model": request.model,
        }


@api_router.get("/config/defaults", tags=["分析"], summary="查询 WebUI 默认模型配置")
async def api_config_defaults(user: dict = Depends(current_user)):
    """返回 WebUI 可用的模型/Base URL 默认值（不含密钥）。"""
    config = load_config()
    is_admin = (not config.multi_user) or user.get("role") == "admin"
    return {
        "success": True,
        "llm_provider": DEFAULT_CONFIG.get("llm_provider", "anthropic"),
        "deep_think_llm": DEFAULT_CONFIG.get("deep_think_llm", "MiniMax-M3"),
        "quick_think_llm": DEFAULT_CONFIG.get(
            "quick_think_llm", "MiniMax-M3.1-Flash"
        ),
        "backend_url": DEFAULT_CONFIG.get("backend_url") if is_admin else None,
        "max_debate_rounds": DEFAULT_CONFIG.get("max_debate_rounds", 1),
        "max_risk_discuss_rounds": DEFAULT_CONFIG.get(
            "max_risk_discuss_rounds", 1
        ),
        "provider_base_urls": PROVIDER_BASE_URLS,
        "provider_models": PROVIDER_DEFAULT_MODELS,
        "parallel_analysts": os.getenv(
            "TRADINGAGENTS_PARALLEL_ANALYSTS", "1"
        ).lower()
        in ("1", "true", "yes", "on"),
        "parallel_analysts_workers": _env_int(
            "TRADINGAGENTS_PARALLEL_ANALYSTS_WORKERS", 4
        ),
    }


@api_router.get(
    "/pipeline/presets",
    tags=["分析"],
    summary="查询可用的图编排预设",
    response_model=PipelinePresetsResponse,
)
async def api_pipeline_presets():
    """返回可用的图编排预设与分析师元数据。"""
    return {
        "success": True,
        "presets": PIPELINE_PRESETS,
        "analysts": ANALYST_META,
    }


@api_router.get("/settings/status", tags=["分析"], summary="查询各 LLM 提供商的密钥配置状态")
async def api_settings_status():
    """返回各 LLM 提供商的密钥配置状态（不返回密钥本身）。"""
    # 重新读取 .env，让新添加的 TAVILY_API_KEY 等无需重启即可显示
    load_dotenv()
    from tradingagents.llm_clients.auth import resolve_api_key

    providers = ["anthropic", "openai", "google", "xai", "openrouter", "ollama"]
    items = []
    for provider in providers:
        configured = provider == "ollama" or bool(resolve_api_key(provider))
        # MiniMax 的 Anthropic 兼容端点也可只用 MINIMAX_API_KEY
        if provider == "anthropic" and os.getenv("MINIMAX_API_KEY"):
            configured = True
        items.append({"provider": provider, "configured": configured})

    return {
        "success": True,
        "providers": items,
        "minimax_configured": bool(os.getenv("MINIMAX_API_KEY")),
        "tavily_configured": bool(
            os.getenv("TAVILY_API_KEY") or os.getenv("TAVILY_API_KEYS")
        ),
        "default_provider": DEFAULT_CONFIG.get("llm_provider"),
    }


@api_router.get(
    "/scheduler/status",
    tags=["任务"],
    summary="查询调度器状态",
    response_model=SchedulerStatusResponse,
)
async def api_scheduler_status():
    return {
        "enabled": os.getenv("SCHEDULE_ENABLED", "0"),
        "time": os.getenv("SCHEDULE_TIME", "18:00"),
        "stocks": os.getenv("SCHEDULE_STOCKS", ""),
        "scheduled_jobs": len(list_scheduled_jobs()),
    }


@api_router.get(
    "/scheduled",
    tags=["任务"],
    summary="查询定时任务列表",
    response_model=ItemListResponse,
)
async def api_list_scheduled():
    return {"success": True, "items": list_scheduled_jobs()}


@api_router.post("/scheduled", tags=["任务"], summary="新增定时任务")
async def api_add_scheduled(request: ScheduledJobRequest):
    try:
        item = add_scheduled_job(
            request.ticker,
            request.name,
            request.schedule_time,
        )
        return {"success": True, "item": item}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.patch("/scheduled/{job_id}", tags=["任务"], summary="更新定时任务")
async def api_update_scheduled(
    job_id: int, request: ScheduledJobUpdateRequest
):
    try:
        item = update_scheduled_job(
            job_id,
            name=request.name,
            schedule_time=request.schedule_time,
            enabled=request.enabled,
        )
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))
    if not item:
        return error_payload("定时任务不存在", code="not_found")
    return {"success": True, "item": item}


@api_router.delete(
    "/scheduled/{job_id}",
    tags=["任务"],
    summary="删除定时任务",
    response_model=SuccessMessageResponse,
)
async def api_delete_scheduled(job_id: int):
    removed = delete_scheduled_job(job_id)
    if not removed:
        return error_payload("定时任务不存在", code="not_found")
    return {"success": True, "message": "已删除"}


# ============================================================================
# 报告中心 / 交易日历 API
# ============================================================================

async def healthz():
    """存活探针：只表示进程还活着，不做任何依赖检查。"""
    return {"status": "ok"}


def _report_owner(user: dict[str, Any] | None) -> str | None:
    if not load_config().multi_user or _is_admin_user(user):
        return None
    return (user or {}).get("username") or ""


@api_router.get(
    "/reports",
    tags=["报告"],
    summary="查询分析报告列表",
    response_model=ReportListResponse,
)
async def api_list_reports(
    ticker: str | None = None,
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(current_user),
):
    return {
        "success": True,
        "reports": list_reports(
            ticker=ticker,
            limit=limit,
            offset=offset,
            owner=_report_owner(user),
        ),
    }


@api_router.get("/reports/{report_id}", tags=["报告"], summary="查询分析报告详情")
async def api_get_report(report_id: int, user: dict = Depends(current_user)):
    report = get_report(report_id, owner=_report_owner(user))
    if not report:
        return error_payload("报告不存在", code="not_found")
    return {"success": True, "report": report}


@api_router.delete(
    "/reports/{report_id}",
    tags=["报告"],
    summary="删除分析报告",
    response_model=SuccessMessageResponse,
)
async def api_delete_report(report_id: int, user: dict = Depends(current_user)):
    deleted = delete_report(report_id, owner=_report_owner(user))
    if not deleted:
        return error_payload("报告不存在", code="not_found")
    return {"success": True, "message": "已删除"}


@api_router.get(
    "/trade-calendar/check",
    tags=["报告"],
    summary="校验交易日",
    response_model=TradeCalendarResponse,
)
async def api_check_trade_date(company: str, date: str):
    ok, message, adjusted = validate_analysis_date(company, date)
    return {
        "success": True,
        "is_trading_day": ok,
        "is_cn_symbol": is_cn_symbol(company),
        "message": message,
        "suggested_date": adjusted,
    }


# ============================================================================
# 组合管理 / 组合级风控 / 运行指标 API
# ============================================================================


@api_router.get("/portfolio", tags=["组合"], summary="查询组合持仓与风险体检")
async def api_portfolio(
    account: str | None = None, user: dict = Depends(current_user)
):
    """返回账户持仓、组合权重与风险体检结果。"""
    try:
        from tradingagents.services.portfolio import portfolio_snapshot

        name = _resolve_account(account, user)
        snapshot = await run_in_threadpool(portfolio_snapshot, name)
        return {"success": True, "portfolio": snapshot}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/portfolio/accounts", tags=["组合"], summary="查询所有账户")
async def api_portfolio_accounts(user: dict = Depends(current_user)):
    try:
        if load_config().multi_user and not _is_admin_user(user):
            items = await run_in_threadpool(
                list_accounts, (user or {}).get("username") or ""
            )
        else:
            items = await run_in_threadpool(list_accounts)
        return {"success": True, "items": items}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.post("/portfolio/refresh", tags=["组合"], summary="刷新持仓最新价")
async def api_portfolio_refresh(
    request: PortfolioAccountRequest | None = None,
    user: dict = Depends(current_user),
):
    """用全市场快照刷新持仓最新价。

    WHY：``last_price`` 此前只在录入/成交时写入，组合市值和风控会一直
    停留在旧价格。刷新后同时返回更新后的组合快照，前端无需再请求一次。
    """
    try:
        from tradingagents.services.portfolio import refresh_prices

        account = _resolve_account(request.account if request else None, user)
        result = await run_in_threadpool(refresh_prices, account)
        return {"success": True, **result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.post("/portfolio/snapshot", tags=["组合"], summary="记录组合每日快照")
async def api_portfolio_snapshot(
    request: PortfolioSnapshotRequest | None = None,
    user: dict = Depends(current_user),
):
    try:
        from tradingagents.services.portfolio import record_snapshot

        account = _resolve_account(request.account if request else None, user)
        snapshot_date = request.date if request else None
        result = await run_in_threadpool(
            record_snapshot,
            account,
            snapshot_date,
            (user or {}).get("username") or "",
        )
        return {"success": True, **result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get(
    "/portfolio/performance", tags=["组合"], summary="查询组合绩效曲线与指标"
)
async def api_portfolio_performance(
    account: str | None = None,
    limit: int = 500,
    user: dict = Depends(current_user),
):
    try:
        from tradingagents.services.portfolio import portfolio_performance

        account = _resolve_account(account, user)
        result = await run_in_threadpool(
            portfolio_performance, account, max(2, min(int(limit), 2000))
        )
        return {"success": True, **result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get(
    "/portfolio/transactions", tags=["组合"], summary="查询组合成交流水"
)
async def api_portfolio_transactions(
    account: str | None = None,
    limit: int = 200,
    user: dict = Depends(current_user),
):
    try:
        from tradingagents.storage import list_transactions

        account = _resolve_account(account, user)
        rows = await run_in_threadpool(
            list_transactions, account, max(1, min(int(limit), 1000))
        )
        return {"success": True, "items": rows}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get(
    "/portfolio/risk", tags=["组合"], summary="组合风险、压力测试与再平衡"
)
async def api_portfolio_risk(
    account: str | None = None,
    lookback: int = 252,
    benchmark: str = "000300",
    rebalance_method: str = "equal",
    user: dict = Depends(current_user),
):
    try:
        from tradingagents.services.portfolio_risk import portfolio_risk_report

        account = _resolve_account(account, user)
        result = await run_in_threadpool(
            portfolio_risk_report,
            account,
            lookback=max(60, min(int(lookback), 1000)),
            benchmark=benchmark,
            rebalance_method=rebalance_method,
        )
        return {"success": bool(result.get("success")), **result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.post(
    "/portfolio/rebalance", tags=["组合"], summary="生成再平衡建议"
)
async def api_portfolio_rebalance(
    request: RebalanceRequest, user: dict = Depends(current_user)
):
    try:
        from tradingagents.services.portfolio_risk import suggest_rebalance
        from tradingagents.services.risk_engine import RiskLimits

        request.account = _resolve_account(request.account, user)
        limits = RiskLimits()
        if request.max_single_weight is not None:
            limits.max_single_weight = request.max_single_weight
        if request.max_total_weight is not None:
            limits.max_total_weight = request.max_total_weight
        result = await run_in_threadpool(
            suggest_rebalance,
            request.account,
            method=request.method,
            limits=limits,
        )
        return {"success": bool(result.get("success")), **result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


class PositionUpsertRequest(BaseModel):
    ticker: str = Field(..., min_length=1)
    quantity: float = Field(..., ge=0)
    avg_cost: float = Field(..., ge=0)
    name: str = ""
    sector: str = ""
    stop_loss: float | None = None
    take_profit: float | None = None
    account: str | None = None
    #: 可选：同时设置账户现金余额。
    #: 手工录入真实持仓时，现金不会自动扣减，若不设置会导致组合权益虚高
    #: （权益 = 现金 + 持仓市值）。首次建账时把真实可用资金填进来即可。
    cash: float | None = Field(default=None, ge=0)


@api_router.post(
    "/portfolio/position", tags=["组合"], summary="录入或更新一条持仓"
)
async def api_upsert_position(
    request: PositionUpsertRequest, user: dict = Depends(current_user)
):
    """手工录入 / 修正持仓。

    WHY：持仓建议与止损止盈提醒都依赖真实持仓数据。此前只有只读接口，
    用户没有便捷入口录入持仓，导致这两项功能实际上用不起来。

    ``last_price`` 与成本价一致（首次录入），后续可通过成交接口或行情刷新。
    """
    try:
        from tradingagents.services.portfolio import ensure_account
        from tradingagents.storage import upsert_position

        account = _resolve_account(request.account, user)
        # 账户按需创建：用户第一次录入持仓时不应该先手动"开户"
        await run_in_threadpool(
            ensure_account, account, 100000.0, (user or {}).get("username") or ""
        )
        row = await run_in_threadpool(
            upsert_position,
            account,
            request.ticker,
            quantity=request.quantity,
            avg_cost=request.avg_cost,
            last_price=request.avg_cost,
            name=request.name,
            sector=request.sector,
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
        )
        if request.cash is not None:
            from tradingagents.storage import update_account_cash

            await run_in_threadpool(
                update_account_cash, account, float(request.cash)
            )
        return {"success": True, "position": row}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.delete(
    "/portfolio/position/{ticker}", tags=["组合"], summary="删除一条持仓"
)
async def api_delete_position(
    ticker: str,
    account: str | None = None,
    user: dict = Depends(current_user),
):
    try:
        from tradingagents.storage import delete_position

        account = _resolve_account(account, user)
        deleted = await run_in_threadpool(delete_position, account, ticker)
        return {
            "success": bool(deleted),
            "message": "已删除" if deleted else "未找到该持仓",
        }
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


class FillRequest(BaseModel):
    ticker: str = Field(..., min_length=1)
    side: Literal["BUY", "SELL"]
    quantity: float = Field(..., gt=0)
    price: float = Field(..., gt=0)
    fee: float = 0.0
    note: str = ""
    trade_date: str = ""
    sector: str = ""
    stop_loss: float | None = None
    take_profit: float | None = None
    account: str | None = None


@api_router.post("/portfolio/fill", tags=["组合"], summary="记录一笔买卖成交")
async def api_record_fill(
    request: FillRequest,
    user: dict = Depends(current_user),
    _risk: None = Depends(require_risk_ack),
):
    """记录成交并自动更新持仓与现金（移动加权平均成本）。"""
    try:
        from tradingagents.services.portfolio import record_fill

        request.account = _resolve_account(request.account, user)
        result = await run_in_threadpool(
            record_fill,
            request.account,
            request.ticker,
            request.side,
            request.quantity,
            request.price,
            request.fee,
            request.note,
            request.trade_date,
            request.sector,
            request.stop_loss,
            request.take_profit,
            owner=(user or {}).get("username") or "",
        )
        return {"success": True, **result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


class PortfolioEvaluateRequest(BaseModel):
    plan: dict = Field(default_factory=dict)
    summary: dict = Field(default_factory=dict)
    account: str | None = None
    ticker: str = ""
    sector: str = ""


@api_router.post("/portfolio/evaluate", tags=["组合"], summary="在组合语境下评估决策卡")
async def api_portfolio_evaluate(
    request: PortfolioEvaluateRequest, user: dict = Depends(current_user)
):
    """把一份决策卡放到组合语境下重新评估，给出真正可执行的仓位。"""
    try:
        from tradingagents.services.portfolio import (
            ensure_account,
            evaluate_plan_for_account,
        )

        request.account = _resolve_account(request.account, user)
        await run_in_threadpool(
            ensure_account,
            request.account,
            100000.0,
            (user or {}).get("username") or "",
        )
        result = await run_in_threadpool(
            evaluate_plan_for_account,
            request.plan,
            request.summary,
            request.account,
            request.ticker,
            request.sector,
        )
        return {"success": True, "evaluation": result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


# ============================================================================
# A 股估值 / 事件 / 财务排雷 / 持仓建议 / 情绪历史 API
#
# 这些是"把数据变成判断"的接口：
#   - 估值分位回答"贵不贵"
#   - 业绩预告回答"有没有超预期"
#   - 财务排雷回答"会不会爆雷"
#   - 事件日历回答"接下来有什么大事"
#   - 持仓建议回答"我手里这只现在该怎么办"
# ============================================================================


@api_router.get("/valuation/percentile", tags=["估值"], summary="查询估值历史分位")
async def api_valuation_percentile(
    ticker: str, years: int = 5, curr_date: str | None = None
):
    """返回 PE-TTM / PB 当前值在近 N 年历史中的分位。"""
    try:
        from tradingagents.dataflows.ashare_events import (
            get_valuation_percentile,
        )

        result = await run_in_threadpool(
            get_valuation_percentile, ticker, curr_date, int(years)
        )
        return {"success": bool(result.get("success")), "valuation": result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/earnings/forecast", tags=["估值"], summary="查询业绩预告与快报")
async def api_earnings_forecast(ticker: str, curr_date: str | None = None):
    try:
        from tradingagents.dataflows.ashare_events import get_earnings_forecast

        result = await run_in_threadpool(
            get_earnings_forecast, ticker, curr_date
        )
        return {"success": bool(result.get("success")), "forecast": result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get(
    "/fundamentals/red-flags", tags=["估值"], summary="财务排雷四项检查"
)
async def api_financial_red_flags(ticker: str, curr_date: str | None = None):
    """商誉 / 股权质押 / 应收账款 / 经营现金流 风险排查。"""
    try:
        from tradingagents.dataflows.ashare_events import (
            get_financial_red_flags,
        )

        result = await run_in_threadpool(
            get_financial_red_flags, ticker, curr_date
        )
        return {"success": bool(result.get("success")), "red_flags": result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/events/calendar", tags=["估值"], summary="未来事件日历")
async def api_event_calendar(ticker: str, days: int = 30):
    """解禁 / 财报披露 / 除权除息 / 业绩预告窗口合并日历。"""
    try:
        from tradingagents.dataflows.ashare_events import build_event_calendar

        result = await run_in_threadpool(
            build_event_calendar, ticker, int(days)
        )
        return {"success": bool(result.get("success")), "calendar": result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/position/advice", tags=["组合"], summary="按当前持仓给出操作建议")
async def api_position_advice(
    ticker: str,
    account: str | None = None,
    decision: str | None = None,
    user: dict = Depends(current_user),
):
    """结合持仓与最新决策卡，给出加仓/减仓/清仓/持有建议。

    ``decision`` 可选（BUY / OVERWEIGHT / HOLD / UNDERWEIGHT / SELL）。
    不传时使用策略扫描得到的规则初判，作为没有 LLM 结论时的兜底。
    """
    try:
        from tradingagents.services.decision_plan import build_decision_plan
        from tradingagents.services.position_advisor import advise_position
        from tradingagents.strategies.scanner import scan_all_strategies

        strategy_result = await run_in_threadpool(scan_all_strategies, ticker)
        summary = (strategy_result or {}).get("summary") or {}

        if decision:
            chosen = str(decision).upper()
        else:
            from tradingagents.services.fast_preview import (
                preliminary_decision,
            )

            chosen = preliminary_decision(summary)

        plan = build_decision_plan(strategy_result, chosen)
        account = _resolve_account(account, user)
        advice = await run_in_threadpool(
            advise_position,
            ticker,
            plan,
            account,
            owner="" if _is_admin_user(user) else (user or {}).get("username") or "",
        )
        return {
            "success": bool(advice.get("success")),
            "advice": advice,
            "decision_source": "manual" if decision else "rule_preview",
        }
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/portfolio/alerts", tags=["组合"], summary="持仓止损止盈提醒")
async def api_portfolio_alerts(
    account: str | None = None, user: dict = Depends(current_user)
):
    """对当前持仓做止损/止盈/深亏/未设止损检查。"""
    try:
        from tradingagents.services.portfolio import load_positions
        from tradingagents.services.position_advisor import (
            check_position_alerts,
            summarize_alerts,
        )

        account = _resolve_account(account, user)
        positions = await run_in_threadpool(load_positions, account)
        alerts = await run_in_threadpool(check_position_alerts, positions)
        return {
            "success": True,
            "alerts": alerts,
            "summary": summarize_alerts(alerts),
        }
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get(
    "/market/sentiment/history", tags=["市场"], summary="市场情绪历史趋势"
)
async def api_sentiment_history(days: int = 60):
    """返回每日市场情绪序列、当前所处状态（冰点/高潮）与趋势。"""
    try:
        from tradingagents.services.market_sentiment import (
            get_sentiment_history,
        )

        return await run_in_threadpool(get_sentiment_history, int(days))
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.post(
    "/market/sentiment/snapshot", tags=["市场"], summary="立即记录一次情绪快照"
)
async def api_sentiment_snapshot(date: str | None = None):
    """把当前市场复盘数据落库为一条情绪快照（用于积累历史序列）。"""
    try:
        from tradingagents.services.market_sentiment import (
            snapshot_market_sentiment,
        )

        return await run_in_threadpool(snapshot_market_sentiment, date)
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/runs", tags=["任务"], summary="查询最近运行指标")
async def api_list_runs(ticker: str | None = None, limit: int = 50, offset: int = 0):
    """返回最近的运行指标（token / 成本 / 耗时）。"""
    try:
        from tradingagents.storage import list_run_logs

        logs = await run_in_threadpool(
            list_run_logs, ticker, int(limit), int(offset)
        )
        return {"success": True, "runs": logs}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/runs/budget", tags=["任务"], summary="查询单次运行预算策略")
async def api_runs_budget():
    try:
        from tradingagents.services.observability import (
            budget_policy,
            budget_status,
        )

        return {
            "success": True,
            "policy": budget_policy(),
            "current": budget_status(),
        }
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/runs/aggregate", tags=["任务"], summary="查询聚合运行指标")
async def api_aggregate_runs():
    """聚合运行指标：总 token / 总成本 / 按模型与来源拆分。"""
    try:
        from tradingagents.storage import aggregate_run_logs

        aggregate = await run_in_threadpool(aggregate_run_logs)
        return {"success": True, "aggregate": aggregate}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


# ============================================================================
# 盘中实时行情 / 告警中心 API
# ============================================================================


@api_router.get("/realtime/session", tags=["实时"], summary="查询当前交易时段")
async def api_realtime_session():
    try:
        from tradingagents.dataflows.realtime import market_session

        return {"success": True, **market_session()}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/realtime/quote", tags=["实时"], summary="查询实时行情")
async def api_realtime_quote(
    ticker: str | None = None, tickers: str | None = None
):
    try:
        from tradingagents.dataflows.ashare_rules import normalize_code
        from tradingagents.dataflows.realtime import get_realtime_quotes

        wanted: list[str] = []
        if ticker:
            wanted.append(ticker)
        if tickers:
            wanted.extend(
                item.strip() for item in tickers.replace("，", ",").split(",")
            )
        codes = list(
            dict.fromkeys(
                code
                for code in (normalize_code(item) for item in wanted)
                if code
            )
        )
        if not codes:
            return error_payload("请提供 ticker 或 tickers", code="bad_request")
        if len(codes) > 200:
            return error_payload("一次最多查询 200 只标的", code="bad_request")
        quotes = await run_in_threadpool(get_realtime_quotes, codes)
        return {"success": True, "items": quotes}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/realtime/intraday", tags=["实时"], summary="查询分钟线")
async def api_realtime_intraday(
    ticker: str, period: str = "5", days: int = 3
):
    try:
        from tradingagents.dataflows.realtime import get_intraday_bars

        items = await run_in_threadpool(
            get_intraday_bars, ticker, period, max(1, min(int(days), 10))
        )
        return {
            "success": True,
            "ticker": ticker,
            "period": period,
            "items": items,
        }
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/realtime/orderbook", tags=["实时"], summary="查询五档盘口")
async def api_realtime_orderbook(ticker: str):
    try:
        from tradingagents.dataflows.realtime import get_order_book

        return {"success": True, **await run_in_threadpool(get_order_book, ticker)}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/alerts/summary", tags=["告警"], summary="查询告警概览")
async def api_alerts_summary():
    try:
        from tradingagents.storage import alert_summary

        return {"success": True, **await run_in_threadpool(alert_summary)}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/alerts/rules", tags=["告警"], summary="查询告警规则")
async def api_alert_rules():
    try:
        from tradingagents.storage import list_alert_rules

        return {
            "success": True,
            "items": await run_in_threadpool(list_alert_rules),
        }
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.post("/alerts/rules", tags=["告警"], summary="新增/更新告警规则")
async def api_add_alert_rule(request: AlertRuleRequest):
    try:
        from tradingagents.dataflows.ashare_rules import normalize_code
        from tradingagents.services.alert_engine import RULE_TYPES
        from tradingagents.storage import add_alert_rule

        if request.rule_type not in RULE_TYPES:
            return error_payload(
                f"不支持的规则类型：{request.rule_type}", code="bad_request"
            )
        ticker = normalize_code(request.ticker) or request.ticker
        rule = await run_in_threadpool(
            add_alert_rule,
            ticker,
            request.rule_type,
            request.threshold,
            name=request.name,
            cooldown_minutes=request.cooldown_minutes,
            note=request.note,
            enabled=request.enabled,
        )
        return {"success": True, "item": rule}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.patch("/alerts/rules/{rule_id}", tags=["告警"], summary="更新告警规则")
async def api_update_alert_rule(rule_id: int, request: AlertRuleUpdateRequest):
    try:
        from tradingagents.storage import update_alert_rule

        rule = await run_in_threadpool(
            update_alert_rule,
            rule_id,
            threshold=request.threshold,
            cooldown_minutes=request.cooldown_minutes,
            enabled=request.enabled,
            name=request.name,
            note=request.note,
        )
        if not rule:
            return error_payload("规则不存在", code="not_found")
        return {"success": True, "item": rule}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.delete(
    "/alerts/rules/{rule_id}", tags=["告警"], summary="删除告警规则"
)
async def api_delete_alert_rule(rule_id: int):
    try:
        from tradingagents.storage import delete_alert_rule

        removed = await run_in_threadpool(delete_alert_rule, rule_id)
        return {
            "success": bool(removed),
            "message": "已删除" if removed else "规则不存在",
        }
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/alerts/events", tags=["告警"], summary="查询告警事件")
async def api_alert_events(
    limit: int = 100,
    unacked_only: bool = False,
    ticker: str | None = None,
):
    try:
        from tradingagents.storage import list_alert_events

        items = await run_in_threadpool(
            list_alert_events,
            max(1, min(int(limit), 1000)),
            unacked_only=bool(unacked_only),
            ticker=ticker,
        )
        return {"success": True, "items": items}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.post(
    "/alerts/events/{event_id}/ack", tags=["告警"], summary="确认单条告警"
)
async def api_ack_alert_event(event_id: int):
    try:
        from tradingagents.storage import ack_alert_event

        ok = await run_in_threadpool(ack_alert_event, event_id)
        return {
            "success": bool(ok),
            "message": "已确认" if ok else "事件不存在",
        }
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.post(
    "/alerts/events/ack-all", tags=["告警"], summary="确认全部告警"
)
async def api_ack_all_alert_events():
    try:
        from tradingagents.storage import ack_all_alert_events

        count = await run_in_threadpool(ack_all_alert_events)
        return {"success": True, "acknowledged": count}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.post("/alerts/check", tags=["告警"], summary="立即检查一次告警")
async def api_check_alerts(request: AlertCheckRequest):
    try:
        from tradingagents.services.alert_engine import check_alerts

        result = await run_in_threadpool(
            check_alerts,
            notify=request.notify,
            tickers=request.tickers,
            include_portfolio=request.include_portfolio,
            account=request.account,
        )
        return {"success": True, **result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


# ============================================================================
# 宏观 / 两融 / 行业轮动 / ETF API
# ============================================================================


@api_router.get("/macro/overview", tags=["宏观"], summary="宏观指标概览")
async def api_macro_overview():
    try:
        from tradingagents.dataflows.macro_data import get_macro_indicators

        result = await run_in_threadpool(get_macro_indicators)
        return {"success": bool(result.get("success")), **result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/macro/margin", tags=["宏观"], summary="两融余额与变化")
async def api_macro_margin(days: int = 60):
    try:
        from tradingagents.dataflows.macro_data import get_margin_snapshot

        result = await run_in_threadpool(
            get_margin_snapshot, max(5, min(int(days), 240))
        )
        return {"success": bool(result.get("success")), **result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/market/rotation", tags=["宏观"], summary="行业轮动排行")
async def api_market_rotation(limit: int = 20):
    try:
        from tradingagents.dataflows.macro_data import get_industry_rotation

        result = await run_in_threadpool(
            get_industry_rotation, max(1, min(int(limit), 50))
        )
        return {"success": bool(result.get("success")), **result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/market/etf-flows", tags=["宏观"], summary="ETF 主力资金流")
async def api_market_etf_flows(limit: int = 20):
    try:
        from tradingagents.dataflows.macro_data import get_etf_flows

        result = await run_in_threadpool(
            get_etf_flows, max(1, min(int(limit), 100))
        )
        return {"success": bool(result.get("success")), **result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/macro/dashboard", tags=["宏观"], summary="宏观/两融/轮动/ETF 面板")
async def api_macro_dashboard():
    try:
        from tradingagents.dataflows.macro_data import get_macro_dashboard

        result = await run_in_threadpool(get_macro_dashboard)
        return {"success": bool(result.get("success")), **result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


# ============================================================================
# 社媒舆情 API
# ============================================================================


@api_router.get("/social/sentiment", tags=["舆情"], summary="个股社媒热度与情绪")
async def api_social_sentiment(
    ticker: str,
    name: str = "",
    curr_date: str | None = None,
    include_xueqiu: bool = False,
):
    try:
        from tradingagents.dataflows.social_sentiment import get_social_sentiment

        result = await run_in_threadpool(
            get_social_sentiment,
            ticker,
            name=name,
            curr_date=curr_date,
            include_xueqiu=bool(include_xueqiu),
        )
        return {"success": bool(result.get("success")), **result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/social/history", tags=["舆情"], summary="社媒舆情历史快照")
async def api_social_history(ticker: str, days: int = 60):
    try:
        from tradingagents.dataflows.social_sentiment import get_social_history

        result = await run_in_threadpool(
            get_social_history, ticker, max(1, min(int(days), 365))
        )
        return {"success": bool(result.get("success")), **result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.post("/social/snapshot", tags=["舆情"], summary="记录一次社媒快照")
async def api_social_snapshot(request: SocialSnapshotRequest):
    try:
        from tradingagents.dataflows.social_sentiment import get_social_sentiment

        result = await run_in_threadpool(
            get_social_sentiment,
            request.ticker,
            name=request.name,
            include_xueqiu=request.include_xueqiu,
            save=True,
        )
        return {"success": bool(result.get("success")), **result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/social/hot", tags=["舆情"], summary="社媒热度榜")
async def api_social_hot(limit: int = 30):
    try:
        from tradingagents.dataflows.social_sentiment import get_social_hot

        result = await run_in_threadpool(
            get_social_hot, max(1, min(int(limit), 100))
        )
        return {"success": bool(result.get("success")), **result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


# ============================================================================
# 公告 / 研报一致预期 / 股东变化 API
# ============================================================================


@api_router.get("/disclosure/summary", tags=["披露"], summary="公告/研报/股东合并摘要")
async def api_disclosure_summary(
    ticker: str, days: int = 30, curr_date: str | None = None
):
    try:
        from tradingagents.dataflows.disclosure import get_disclosure_summary

        result = await run_in_threadpool(
            get_disclosure_summary,
            ticker,
            days=max(1, min(int(days), 180)),
            curr_date=curr_date,
        )
        return {"success": bool(result.get("success")), **result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/disclosure/announcements", tags=["披露"], summary="个股公告")
async def api_disclosure_announcements(
    ticker: str,
    days: int = 30,
    limit: int = 50,
    curr_date: str | None = None,
):
    try:
        from tradingagents.dataflows.disclosure import get_announcements

        result = await run_in_threadpool(
            get_announcements,
            ticker,
            days=max(1, min(int(days), 180)),
            limit=max(1, min(int(limit), 200)),
            curr_date=curr_date,
        )
        return {"success": bool(result.get("success")), **result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/disclosure/research", tags=["披露"], summary="研报一致预期")
async def api_disclosure_research(
    ticker: str, days: int = 180, curr_date: str | None = None
):
    try:
        from tradingagents.dataflows.disclosure import get_research_consensus

        result = await run_in_threadpool(
            get_research_consensus,
            ticker,
            days=max(1, min(int(days), 730)),
            curr_date=curr_date,
        )
        return {"success": bool(result.get("success")), **result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/disclosure/shareholders", tags=["披露"], summary="股东变化")
async def api_disclosure_shareholders(
    ticker: str, curr_date: str | None = None
):
    try:
        from tradingagents.dataflows.disclosure import get_shareholder_changes

        result = await run_in_threadpool(
            get_shareholder_changes, ticker, curr_date=curr_date
        )
        return {"success": bool(result.get("success")), **result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


# ============================================================================
# 全市场选股 API
# ============================================================================


def _screener_kwargs(request: ScreenerRequest) -> dict[str, Any]:
    return {
        "top": request.top,
        "style": request.style,
        "min_amount_yi": request.min_amount_yi,
        "min_market_cap_yi": request.min_market_cap_yi,
        "max_market_cap_yi": request.max_market_cap_yi,
        "exclude_st": request.exclude_st,
        "exclude_limit": request.exclude_limit,
        "exclude_loss": request.exclude_loss,
        "boards": request.boards,
        "include_financials": request.include_financials,
        "include_fund_flow": request.include_fund_flow,
        "industry_neutral": request.industry_neutral,
        "save": request.save,
    }


@api_router.get("/screener/styles", tags=["选股"], summary="查询选股风格与权重")
async def api_screener_styles():
    try:
        from tradingagents.services.screener import SCREENER_STYLES

        return {"success": True, "items": SCREENER_STYLES}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/screener", tags=["选股"], summary="全市场多因子选股")
async def api_screener(
    top: int = 30,
    style: str = "balanced",
    min_amount_yi: float = 1.0,
    min_market_cap_yi: float = 30.0,
    max_market_cap_yi: float | None = None,
    exclude_st: bool = True,
    exclude_limit: bool = True,
    exclude_loss: bool = False,
    include_financials: bool = True,
    include_fund_flow: bool = False,
    industry_neutral: bool = False,
    save: bool = False,
):
    try:
        from tradingagents.services.screener import run_screener

        result = await run_in_threadpool(
            run_screener,
            top=max(1, min(int(top), 200)),
            style=style,
            min_amount_yi=max(0.0, float(min_amount_yi)),
            min_market_cap_yi=max(0.0, float(min_market_cap_yi)),
            max_market_cap_yi=max_market_cap_yi,
            exclude_st=bool(exclude_st),
            exclude_limit=bool(exclude_limit),
            exclude_loss=bool(exclude_loss),
            include_financials=bool(include_financials),
            include_fund_flow=bool(include_fund_flow),
            industry_neutral=bool(industry_neutral),
            save=bool(save),
        )
        return {**result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.post("/screener/run", tags=["选股"], summary="按完整参数运行选股")
async def api_screener_run(request: ScreenerRequest):
    try:
        from tradingagents.services.screener import run_screener

        result = await run_in_threadpool(
            run_screener, **_screener_kwargs(request)
        )
        return {**result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get(
    "/screener/latest", tags=["选股"], summary="查询最近一次选股结果"
)
async def api_screener_latest(
    run_date: str | None = None, style: str | None = None
):
    try:
        from tradingagents.storage import get_latest_screener_run

        result = await run_in_threadpool(
            get_latest_screener_run, run_date, style
        )
        if not result:
            return {
                "success": True,
                "run": None,
                "message": "暂无选股记录",
            }
        return {"success": True, "run": result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/screener/history", tags=["选股"], summary="查询选股历史")
async def api_screener_history(limit: int = 20):
    try:
        from tradingagents.storage import list_screener_runs

        items = await run_in_threadpool(
            list_screener_runs, max(1, min(int(limit), 200))
        )
        return {"success": True, "items": items}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get(
    "/screener/runs/{run_id}", tags=["选股"], summary="查询某次选股详情"
)
async def api_screener_run_detail(run_id: int):
    try:
        from tradingagents.storage import get_screener_run

        result = await run_in_threadpool(get_screener_run, run_id)
        if not result:
            return error_payload("选股记录不存在", code="not_found")
        return {"success": True, "run": result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.delete(
    "/screener/runs/{run_id}", tags=["选股"], summary="删除选股记录"
)
async def api_screener_delete(run_id: int):
    try:
        from tradingagents.storage import delete_screener_run

        removed = await run_in_threadpool(delete_screener_run, run_id)
        return {
            "success": bool(removed),
            "message": "已删除" if removed else "记录不存在",
        }
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


# ============================================================================
# 本地数据仓库 API（覆盖 / 质量 / 同步）
# ============================================================================


@api_router.get("/data/status", tags=["数据"], summary="查询本地行情仓库状态")
async def api_data_status(ticker: str | None = None):
    try:
        from tradingagents.services.market_data import coverage, market_stats

        payload: dict[str, Any] = {
            "success": True,
            "cache": await run_in_threadpool(market_stats),
        }
        if ticker:
            payload["ticker"] = await run_in_threadpool(coverage, ticker)
        return payload
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/data/symbols", tags=["数据"], summary="列出本地已缓存标的")
async def api_data_symbols():
    try:
        from tradingagents.storage.market_store import list_market_symbols

        return {
            "success": True,
            "items": await run_in_threadpool(list_market_symbols),
        }
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/data/quality", tags=["数据"], summary="检查本地行情数据质量")
async def api_data_quality(
    ticker: str,
    start_date: str | None = None,
    end_date: str | None = None,
):
    try:
        from tradingagents.services.data_quality import quality_report

        result = await run_in_threadpool(
            quality_report, ticker, start_date, end_date
        )
        return {"success": True, **result}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.post("/data/sync", tags=["数据"], summary="同步标的到本地行情库")
async def api_data_sync(request: DataSyncRequest):
    try:
        from tradingagents.dataflows.interface import route_to_vendor
        from tradingagents.services.market_data import sync_symbol

        tickers = [item for item in (request.tickers or []) if str(item).strip()]
        if request.ticker:
            tickers.append(request.ticker)
        tickers = list(dict.fromkeys(str(item).strip() for item in tickers))
        if not tickers:
            return error_payload("请提供 ticker 或 tickers", code="bad_request")
        if len(tickers) > 50:
            return error_payload("一次最多同步 50 只标的", code="bad_request")

        end_date = request.end_date or datetime.now().strftime("%Y-%m-%d")
        start_date = request.start_date or (
            datetime.now() - timedelta(days=365 * 3)
        ).strftime("%Y-%m-%d")

        def _fetch(code: str, start: str, end: str) -> str:
            return route_to_vendor("get_stock_data", code, start, end)

        results = []
        for code in tickers:
            try:
                result = await run_in_threadpool(
                    sync_symbol,
                    code,
                    start_date,
                    end_date,
                    fetch=_fetch,
                    adjust=request.adjust,
                    force=request.force,
                )
                results.append({"success": True, **result})
            except ApiError:
                raise
            except Exception as exc:
                results.append(
                    {
                        "success": False,
                        "ticker": code,
                        "message": f"{type(exc).__name__}: {exc}",
                    }
                )
        return {
            "success": all(item.get("success") for item in results),
            "start_date": start_date,
            "end_date": end_date,
            "items": results,
        }
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


# ============================================================================
# 推荐评估 API（决策 → 前瞻收益）
# ============================================================================


def _parse_horizons(value: str | None) -> list[int] | None:
    if not value:
        return None
    items: list[int] = []
    for part in str(value).replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            number = int(part)
        except ValueError:
            continue
        if 1 <= number <= 250 and number not in items:
            items.append(number)
    return items or None


@api_router.get(
    "/evaluation", tags=["评估"], summary="评估历史推荐的前瞻收益与命中率"
)
async def api_evaluation(
    ticker: str | None = None,
    limit: int = 50,
    horizons: str | None = None,
    user: dict = Depends(current_user),
):
    """把历史报告与之后 5/10/20 个交易日的真实行情对齐。

    GET 只读、不写记忆；需要积累经验时用 ``POST /api/evaluation/run``。
    """
    try:
        from tradingagents.services.evaluation import evaluate_reports

        parsed = _parse_horizons(horizons)
        result = await run_in_threadpool(
            evaluate_reports,
            ticker,
            max(1, min(int(limit), 200)),
            parsed or (5, 10, 20),
            write_memory=False,
            owner=_report_owner(user),
        )
        return {
            "success": True,
            "ticker": ticker or "",
            "horizons": parsed or [5, 10, 20],
            **result,
        }
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.post(
    "/evaluation/run", tags=["评估"], summary="手动运行推荐评估并可回写记忆"
)
async def api_evaluation_run(
    request: EvaluationRequest, user: dict = Depends(current_user)
):
    try:
        from tradingagents.services.evaluation import evaluate_reports

        result = await run_in_threadpool(
            evaluate_reports,
            request.ticker,
            request.limit,
            request.horizons or (5, 10, 20),
            write_memory=request.write_memory,
            owner=_report_owner(user),
        )
        return {
            "success": True,
            "ticker": request.ticker or "",
            "horizons": request.horizons or [5, 10, 20],
            "memory_written": result.get("summary", {}).get("memory_written", 0),
            **result,
        }
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get(
    "/evaluation/report/{report_id}",
    tags=["评估"],
    summary="评估单条历史报告",
)
async def api_evaluation_report(
    report_id: int, user: dict = Depends(current_user)
):
    """从报告详情直接查看"这条推荐后来赚了还是亏了"。"""
    try:
        from tradingagents.services.evaluation import evaluate_decision

        report = await run_in_threadpool(
            get_report, report_id, _report_owner(user)
        )
        if not report:
            return error_payload("报告不存在", code="not_found")
        row = await run_in_threadpool(
            evaluate_decision,
            report.get("ticker") or "",
            report.get("decision") or "",
            report.get("trade_date") or "",
        )
        row["report_id"] = report_id
        row["source"] = report.get("source") or ""
        return {"success": True, "evaluation": row}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


# ============================================================================
# 模拟盘 API（持久化 PaperBroker 状态）
# ============================================================================


@api_router.get("/paper", tags=["模拟盘"], summary="查询模拟盘账户与持仓")
async def api_paper(
    account: str | None = None, user: dict = Depends(current_user)
):
    try:
        with PAPER_STATE_LOCK:
            name, broker = _load_paper_broker(account, user)
            # GET 不落库：查看默认模拟盘不应产生写副作用。
            return _paper_payload(name, broker)
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.get("/paper/accounts", tags=["模拟盘"], summary="查询模拟盘账户列表")
async def api_paper_accounts(user: dict = Depends(current_user)):
    try:
        if load_config().multi_user and not _is_admin_user(user):
            items = list_paper_accounts((user or {}).get("username") or "")
        else:
            items = list_paper_accounts()
        return {"success": True, "items": items}
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.post("/paper/reset", tags=["模拟盘"], summary="新建/重置模拟盘账户")
async def api_paper_reset(
    request: PaperResetRequest, user: dict = Depends(current_user)
):
    try:
        from tradingagents.services.paper_trading import PaperBroker

        name = _paper_account_name(request.account, user)
        owner = "" if _is_admin_user(user) else (user or {}).get("username") or ""
        broker = PaperBroker(initial_cash=request.initial_cash)
        with PAPER_STATE_LOCK:
            _save_paper_broker(name, broker, owner)
        return _paper_payload(name, broker)
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.delete("/paper", tags=["模拟盘"], summary="删除模拟盘账户")
async def api_paper_delete(
    account: str | None = None, user: dict = Depends(current_user)
):
    try:
        with PAPER_STATE_LOCK:
            removed = delete_paper_state(_paper_account_name(account, user))
        return {
            "success": bool(removed),
            "message": "已删除" if removed else "模拟盘账户不存在",
        }
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.post("/paper/order", tags=["模拟盘"], summary="提交模拟委托")
async def api_paper_order(
    request: PaperOrderRequest,
    user: dict = Depends(current_user),
    _risk: None = Depends(require_risk_ack),
):
    try:
        with PAPER_STATE_LOCK:
            name, broker = _load_paper_broker(request.account, user)
            owner = "" if _is_admin_user(user) else (user or {}).get("username") or ""
            order = broker.submit_order(
                request.ticker,
                request.side,
                request.quantity,
                order_type=request.order_type,
                limit_price=request.limit_price,
                name=request.name,
                reason=request.reason,
                note=request.note,
                stop_loss=request.stop_loss,
                take_profit=request.take_profit,
            )
            _save_paper_broker(name, broker, owner)
            return {
                "success": True,
                "order": order.to_dict(),
                "report": broker.report(last_orders=100),
            }
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.post("/paper/cancel", tags=["模拟盘"], summary="撤销模拟委托")
async def api_paper_cancel(
    order_id: str,
    account: str | None = None,
    user: dict = Depends(current_user),
):
    try:
        with PAPER_STATE_LOCK:
            name, broker = _load_paper_broker(account, user)
            owner = "" if _is_admin_user(user) else (user or {}).get("username") or ""
            cancelled = broker.cancel_order(order_id)
            _save_paper_broker(name, broker, owner)
            return {
                "success": bool(cancelled),
                "message": "已撤单" if cancelled else "未找到可撤销的委托",
                "report": broker.report(last_orders=100),
            }
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.post(
    "/paper/from-plan", tags=["模拟盘"], summary="把决策卡转成模拟委托"
)
async def api_paper_from_plan(
    request: PaperPlanRequest,
    user: dict = Depends(current_user),
    _risk: None = Depends(require_risk_ack),
):
    try:
        from tradingagents.services.paper_trading import order_from_decision_plan

        with PAPER_STATE_LOCK:
            name, broker = _load_paper_broker(request.account, user)
            owner = "" if _is_admin_user(user) else (user or {}).get("username") or ""
            order = order_from_decision_plan(
                request.plan,
                request.ticker,
                broker=broker,
                name=request.name,
            )
            _save_paper_broker(name, broker, owner)
            return {
                "success": True,
                "order": order.to_dict() if order else None,
                "message": "已生成模拟委托" if order else "决策为 HOLD/冲突/无可卖持仓，未下单",
                "report": broker.report(last_orders=100),
            }
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.post("/paper/mark", tags=["模拟盘"], summary="按最新价盯市")
async def api_paper_mark(
    request: PaperMarkRequest, user: dict = Depends(current_user)
):
    """盯市：不传 prices 时自动抓全市场快照，更新持仓与净值曲线。"""
    try:
        # 先在锁内只读状态并收集标的，网络取数放到锁外，避免阻塞全站。
        with PAPER_STATE_LOCK:
            name, broker = _load_paper_broker(request.account, user)
            tickers = list(broker.positions().keys())
            tickers += [
                order.ticker
                for order in broker.account().orders
                if order.status == "pending"
            ]
            tickers = sorted({str(item) for item in tickers if item})
        prices = request.prices
        error = ""
        if not prices and tickers:
            try:
                from tradingagents.dataflows.akshare_adapter import (
                    get_akshare_spot_prices,
                )

                prices = await run_in_threadpool(
                    get_akshare_spot_prices, tickers
                )
            except ApiError:
                raise
            except Exception as exc:
                error = str(exc)
        with PAPER_STATE_LOCK:
            # 重新读取，避免覆盖锁外期间的新委托。
            name, broker = _load_paper_broker(request.account, user)
            owner = "" if _is_admin_user(user) else (user or {}).get("username") or ""
            broker.mark_to_market(
                request.date or datetime.now().strftime("%Y-%m-%d"),
                prices or {},
            )
            _save_paper_broker(name, broker, owner)
            return {
                "success": True,
                "prices": prices or {},
                "error": error,
                "report": broker.report(last_orders=100),
            }
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


@api_router.post(
    "/paper/advance", tags=["模拟盘"], summary="用历史日线推进模拟盘撮合"
)
async def api_paper_advance(
    request: PaperAdvanceRequest, user: dict = Depends(current_user)
):
    """按给定日期区间逐日撮合，适合回放策略信号。"""
    try:
        from tradingagents.services.paper_trading import (
            advance_broker,
            load_price_frames,
        )

        try:
            span = (
                datetime.fromisoformat(request.end_date[:10])
                - datetime.fromisoformat(request.start_date[:10])
            ).days
        except ValueError:
            return error_payload("日期格式必须是 YYYY-MM-DD", code="bad_request")
        if span < 0 or span > 366:
            return error_payload("推进区间需在 0-366 天内", code="bad_request")

        with PAPER_STATE_LOCK:
            name, broker = _load_paper_broker(request.account, user)
            tickers = set(broker.positions().keys())
            tickers.update(
                order.ticker
                for order in broker.account().orders
                if order.status == "pending"
            )
            last_date = broker.last_processed_date
        # 不允许重放已经处理过的日期；前端默认“最近两周”时自动从上次之后开始。
        start_date = request.start_date
        if last_date and start_date <= last_date:
            start_date = (
                datetime.fromisoformat(last_date).date() + timedelta(days=1)
            ).isoformat()
        if start_date > request.end_date:
            return error_payload(
                f"开始日期 {start_date} 晚于结束日期 {request.end_date}；"
                "请选择更晚的区间或先重置模拟盘",
                code="bad_request",
            )
        frames = (
            await run_in_threadpool(
                load_price_frames,
                sorted(tickers),
                start_date,
                request.end_date,
            )
            if tickers
            else {}
        )
        with PAPER_STATE_LOCK:
            # 重新读取状态，避免覆盖取数期间新增的委托。
            name, broker = _load_paper_broker(request.account, user)
            if frames:
                # 纯 CPU 撮合：在锁内同步执行，避免持锁 await 导致事件循环死锁。
                advance_broker(broker, frames)
            owner = "" if _is_admin_user(user) else (user or {}).get("username") or ""
            _save_paper_broker(name, broker, owner)
            return {
                "success": True,
                "start_date": start_date,
                "tickers": sorted(tickers),
                "report": broker.report(last_orders=100),
            }
    except ApiError:
        raise
    except Exception as exc:
        return error_payload(safe_exception_message(exc))


# ============================================================================
# 就绪探针 / 指标 / 静态文件服务 + 应用装配
# ============================================================================

logger = logging.getLogger(__name__)

APP_VERSION = "1.0.0"

frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")

#: 就绪探针要求存在的核心数据表。
REQUIRED_DB_TABLES: tuple[str, ...] = (
    "reports",
    "jobs",
    "watchlist",
    "scheduled_jobs",
    "briefings",
    "run_logs",
)

#: 进程启动时间（用于 /readyz 的 uptime）。
_PROCESS_STARTED_AT = time.time()


def _ensure_db_schema() -> None:
    """确保核心数据表存在（幂等操作，失败时由调用方兜底）。"""
    from tradingagents.storage import init_db

    init_db()
    for module_name, func_name in (
        ("tradingagents.storage.job_store", "_init_jobs"),
        ("tradingagents.storage.watchlist_store", "_init_watchlist"),
        ("tradingagents.storage.scheduled_store", "_init_scheduled"),
        ("tradingagents.storage.briefing_store", "_init_briefings"),
        ("tradingagents.storage.run_log_store", "_init_run_logs"),
    ):
        try:
            module = importlib.import_module(module_name)
            init_func = getattr(module, func_name, None)
            if callable(init_func):
                init_func()
        except Exception:
            logger.debug(
                "初始化数据表 %s.%s 失败", module_name, func_name, exc_info=True
            )


def _check_database() -> dict[str, Any]:
    """数据库检查：能否打开 + 核心表是否存在。"""
    try:
        from tradingagents.storage.report_store import db_path

        path = db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(path), timeout=5) as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        tables = {str(row[0]) for row in rows}
        missing = [name for name in REQUIRED_DB_TABLES if name not in tables]
        if missing:
            _ensure_db_schema()
            with sqlite3.connect(str(path), timeout=5) as conn:
                rows = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            tables = {str(row[0]) for row in rows}
            missing = [name for name in REQUIRED_DB_TABLES if name not in tables]
        if missing:
            return {
                "name": "database",
                "status": "failed",
                "required": True,
                "detail": f"缺少数据表: {', '.join(missing)}",
            }
        return {
            "name": "database",
            "status": "ok",
            "required": True,
            "detail": f"{path}（{len(tables)} 张表）",
        }
    except ApiError:
        raise
    except Exception as exc:
        return {
            "name": "database",
            "status": "failed",
            "required": True,
            "detail": f"{type(exc).__name__}: {exc}",
        }


def _check_scheduler() -> dict[str, Any]:
    """调度器检查：仅在 SCHEDULE_ENABLED 开启时要求线程存活。"""
    enabled = os.getenv("SCHEDULE_ENABLED", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not enabled:
        return {
            "name": "scheduler",
            "status": "skipped",
            "required": False,
            "detail": "SCHEDULE_ENABLED 未开启，跳过调度器检查",
        }
    try:
        from tradingagents import scheduler as scheduler_module

        thread = getattr(scheduler_module, "_thread", None)
        alive = bool(thread is not None and thread.is_alive())
        if alive:
            return {
                "name": "scheduler",
                "status": "ok",
                "required": True,
                "detail": "定时任务线程运行中",
            }
        return {
            "name": "scheduler",
            "status": "failed",
            "required": True,
            "detail": "定时任务线程未运行",
        }
    except ApiError:
        raise
    except Exception as exc:
        return {
            "name": "scheduler",
            "status": "failed",
            "required": True,
            "detail": f"{type(exc).__name__}: {exc}",
        }


def _check_disk() -> dict[str, Any]:
    """磁盘检查：数据库所在分区的剩余空间（低于 500MB 视为告警）。"""
    try:
        from tradingagents.storage.report_store import db_path

        usage = shutil.disk_usage(str(db_path().parent))
        free_mb = usage.free / (1024 * 1024)
        if free_mb < 500:
            return {
                "name": "disk",
                "status": "failed",
                "required": False,
                "detail": f"剩余空间仅 {free_mb:.0f} MB",
            }
        return {
            "name": "disk",
            "status": "ok",
            "required": False,
            "detail": f"剩余 {free_mb:.0f} MB",
        }
    except ApiError:
        raise
    except Exception as exc:
        return {
            "name": "disk",
            "status": "skipped",
            "required": False,
            "detail": f"{type(exc).__name__}: {exc}",
        }


async def readyz():
    """就绪探针：真实检查数据库、调度器与磁盘，任一必需检查失败返回 503。"""
    checks = [_check_database(), _check_scheduler(), _check_disk()]
    failed = [c for c in checks if c["required"] and c["status"] == "failed"]
    ready = not failed
    content = {
        "status": "ready" if ready else "not_ready",
        "ready": ready,
        "checks": checks,
        "version": APP_VERSION,
        "uptime_seconds": round(time.time() - _PROCESS_STARTED_AT, 3),
    }
    return JSONResponse(status_code=200 if ready else 503, content=content)


async def metrics_endpoint():
    """导出 Prometheus 文本格式的进程内指标。"""
    return PlainTextResponse(
        metrics.render_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


async def serve_frontend():
    index_path = os.path.join(
        os.path.dirname(__file__), "..", "frontend", "index.html"
    )
    return FileResponse(index_path)


def _warn_if_unauthenticated(config: ApiConfig) -> None:
    """鉴权关闭且监听非回环地址时，打印醒目安全告警。"""
    if config.auth_enabled:
        logger.info(
            "WebUI API 鉴权已启用（WEBUI_API_KEY 已设置），受保护路由需要携带密钥"
        )
        return
    if config.is_loopback_host:
        logger.info(
            "WebUI API 鉴权未启用（WEBUI_API_KEY 未设置），仅监听回环地址 %s",
            config.host,
        )
        return
    logger.warning(
        "【安全警告】WebUI API 未启用鉴权（WEBUI_API_KEY 未设置），"
        "且服务监听在非回环地址 %s:%s。"
        "任何能访问该地址的人都可以调用分析接口消耗 LLM 额度、读取全部报告、"
        "修改自选股与定时任务、并触发外部 Webhook 推送。"
        "请设置 WEBUI_API_KEY 环境变量，或把 WEBUI_HOST 设为 127.0.0.1 只在本机监听。",
        config.host,
        config.port,
    )


@asynccontextmanager
async def lifespan(application: FastAPI):
    """应用生命周期：启动任务队列，退出时优雅停止。

    定时调度器**只在显式开启时才启动**。

    WHY：此前无论 `SCHEDULE_ENABLED` 是否配置，WebUI 一启动就会拉起调度
    线程；只要库里有到点的定时任务（或触发了补跑），用户"打开页面"就会
    看到系统自己在分析某只股票。现在改为显式 opt-in：不配置
    `SCHEDULE_ENABLED=1` 就完全不会启动调度，分析只能由用户手动触发。
    """
    _warn_if_unauthenticated(load_config())

    # 多用户模式首次启动：创建初始管理员（密码来自 env 或随机写入 data/）。
    try:
        from webui.backend.users import ensure_bootstrap_admin

        ensure_bootstrap_admin()
    except Exception:
        logger.warning("初始化管理员失败", exc_info=True)

    # 启动有界任务队列：分析任务不再直接起线程。
    # recover_pending=False —— 重启不自动执行历史排队任务。
    try:
        ensure_job_queue(
            worker_count=ANALYSIS_WORKERS,
            handlers={"analysis": _analysis_job_handler},
        )
    except Exception:  # 队列启动失败不应阻断服务
        logger.warning("任务队列启动失败", exc_info=True)

    scheduler_enabled = str(
        os.getenv("SCHEDULE_ENABLED", "0")
    ).strip().lower() in ("1", "true", "yes", "on")
    if scheduler_enabled:
        start_scheduler()
        logger.info("定时任务调度器已启动（SCHEDULE_ENABLED=1）")
    else:
        logger.info(
            "未启用定时任务调度器（SCHEDULE_ENABLED 未设置）。"
            "分析只能通过手动触发。"
        )

    try:
        yield
    finally:
        if scheduler_enabled:
            stop_scheduler()
        try:
            from tradingagents.services.job_queue import stop_job_queue

            stop_job_queue(wait=True)
        except Exception:
            logger.warning("任务队列停止失败", exc_info=True)


OPENAPI_TAGS = [
    {"name": "认证", "description": "登录、会话、用户管理、风险确认与审计日志。"},
    {"name": "分析", "description": "深度分析任务的启动、状态与模型连通性。"},
    {"name": "任务", "description": "分析任务、定时任务与运行指标。"},
    {"name": "自选", "description": "自选股维护。"},
    {"name": "策略", "description": "经典策略扫描、回测与预警推送。"},
    {"name": "市场", "description": "行情、K 线、资金流与市场复盘。"},
    {"name": "报告", "description": "分析报告、盘后简报与交易日历。"},
    {"name": "组合", "description": "组合持仓、组合级风控评估与持仓操作建议。"},
    {"name": "实时", "description": "盘中实时行情、分钟线与五档盘口。"},
    {"name": "告警", "description": "价格/涨跌幅/量比告警规则、事件与确认。"},
    {"name": "选股", "description": "全市场多因子选股、每日候选池与历史归档。"},
    {"name": "宏观", "description": "宏观指标、两融余额、行业轮动与 ETF 资金流。"},
    {"name": "舆情", "description": "东财人气榜、概念热度、微博情绪与社媒异常热度。"},
    {"name": "披露", "description": "公告、研报一致预期、股东户数与十大股东。"},
    {"name": "数据", "description": "本地行情仓库、覆盖范围与数据质量检查。"},
    {"name": "评估", "description": "历史推荐的前瞻收益、命中率与置信度校准。"},
    {"name": "模拟盘", "description": "模拟账户、委托、持仓与逐日撮合。"},
    {"name": "估值", "description": "估值历史分位、业绩预告、财务排雷与事件日历。"},
    {"name": "系统", "description": "健康探针、就绪探针与指标导出。"},
]


def create_app(config: ApiConfig | None = None) -> FastAPI:
    """装配 WebUI 应用。

    ``config`` 为 ``None`` 时使用环境变量配置；注意鉴权依赖与限流中间件都是
    **请求期**读取配置的，因此运行期调整环境变量同样可以立即生效。
    """
    current = config or load_config()
    application = FastAPI(
        title="TradingAgents Web UI",
        version=APP_VERSION,
        lifespan=lifespan,
        openapi_tags=OPENAPI_TAGS,
    )
    register_exception_handlers(application)

    # 中间件顺序（越晚添加越靠外层）：
    #   RequestId → CORS → Timing → RateLimit → 路由
    application.add_middleware(TimingMiddleware)
    application.add_middleware(RateLimitMiddleware)
    # ErrorStatus 必须在 Audit 内层：这样审计能看到最终 4xx/5xx 状态。
    application.add_middleware(ErrorStatusMiddleware)
    application.add_middleware(AuditMiddleware)
    application.add_middleware(SecurityHeadersMiddleware)
    if current.cors_enabled:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=list(current.allowed_origins),
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=[
                "X-Request-ID",
                "X-Response-Time-Ms",
                "Retry-After",
            ],
        )
    application.add_middleware(RequestIdMiddleware)

    # 登录路由不挂鉴权依赖；其余 API 默认受保护。
    application.include_router(auth_router, prefix=current.api_prefix)
    application.include_router(api_router, prefix=current.api_prefix)

    # 豁免鉴权 / 限流的系统路由
    application.add_api_route(
        "/healthz",
        healthz,
        methods=["GET"],
        tags=["系统"],
        summary="存活探针",
        response_model=HealthResponse,
    )
    application.add_api_route(
        "/readyz",
        readyz,
        methods=["GET"],
        tags=["系统"],
        summary="就绪探针（检查数据库、调度器与磁盘）",
        response_model=ReadyResponse,
    )
    application.add_api_route(
        "/metrics",
        metrics_endpoint,
        methods=["GET"],
        tags=["系统"],
        summary="导出 Prometheus 文本格式指标",
        response_class=PlainTextResponse,
    )
    application.add_api_route(
        "/",
        serve_frontend,
        methods=["GET"],
        tags=["系统"],
        summary="返回 WebUI 前端页面",
        response_class=FileResponse,
    )
    application.add_api_websocket_route("/ws", websocket_endpoint)

    # 未匹配路由返回统一错误信封，而不是 Starlette 默认的纯文本 404
    application.router.default = NotFoundResponder()

    application.mount("/static", StaticFiles(directory=frontend_dir), name="static")
    return application


#: 进程级应用实例（uvicorn 入口：webui.backend.server:app）。
app = create_app()
