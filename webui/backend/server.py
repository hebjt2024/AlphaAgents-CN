"""
TradingAgents Web UI 后端服务
基于 FastAPI + WebSocket，提供实时流式分析进度推送

架构：
  前端 HTML → HTTP POST /api/analyze → 后端启动分析线程
  分析线程 → 线程安全队列 → 主事件循环 → WebSocket → 前端实时显示进度
  分析完成 → 最终结果通过 WebSocket 推送到前端

启动方式：
  python webui/run_webui.py
"""

import asyncio
import json
import threading
import traceback
from datetime import datetime
from typing import Optional
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from dotenv import load_dotenv

load_dotenv()

# ============================================================================
# FastAPI 应用初始化
# ============================================================================
app = FastAPI(title="TradingAgents Web UI", version="1.0.0")

active_connections: set[WebSocket] = set()
_main_loop: Optional[asyncio.AbstractEventLoop] = None

analysis_status = {
    "running": False,
    "company": "",
    "date": "",
    "start_time": None,
}


# ============================================================================
# 请求/响应模型
# ============================================================================

class AnalyzeRequest(BaseModel):
    company: str = "NVDA"
    date: str = "2024-05-10"
    llm_provider: str = "anthropic"
    deep_think_llm: str = "MiniMax-M3"
    quick_think_llm: str = "MiniMax-M2.5"
    max_debate_rounds: int = 1
    max_risk_discuss_rounds: int = 1


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

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global _main_loop
    _main_loop = asyncio.get_running_loop()

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

def format_message_content(msg) -> str:
    try:
        msg_type = type(msg).__name__
        if msg_type == "HumanMessage":
            return f"[用户] {msg.content}"
        elif msg_type == "AIMessage":
            content = msg.content
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                tool_names = [tc.get("name", "unknown") for tc in msg.tool_calls]
                prefix = f"[AI 调用工具: {', '.join(tool_names)}] "
                if isinstance(content, str):
                    return prefix + content
                elif isinstance(content, list):
                    text_parts = [
                        item.get("text", "") if isinstance(item, dict) else str(item)
                        for item in content
                        if isinstance(item, dict) and item.get("type") == "text"
                    ]
                    return prefix + "\n".join(text_parts)
            if isinstance(content, str):
                return f"[AI] {content}"
            elif isinstance(content, list):
                text_parts = [
                    item.get("text", "") if isinstance(item, dict) else str(item)
                    for item in content
                    if isinstance(item, dict) and item.get("type") == "text"
                ]
                return f"[AI] {''.join(text_parts)}"
            return f"[AI] {str(content)}"
        elif msg_type == "ToolMessage":
            tool_name = getattr(msg, "name", "unknown")
            content = msg.content
            if isinstance(content, str) and len(content) > 200:
                content = content[:200] + "..."
            return f"[工具: {tool_name}] {content}"
        else:
            return f"[{msg_type}] {str(msg.content)}"
    except Exception:
        return str(msg)


def extract_node_name(chunk: dict) -> str:
    if "messages" in chunk and chunk["messages"]:
        last_msg = chunk["messages"][-1]
        if hasattr(last_msg, "name") and last_msg.name:
            return last_msg.name
    return "Processing"


def node_display_name(node_name: str) -> str:
    name_map = {
        "Market Analyst": "市场分析师",
        "Social Media Analyst": "社交媒体分析师",
        "News Analyst": "新闻分析师",
        "Fundamentals Analyst": "基本面分析师",
        "Bull Researcher": "多头研究员",
        "Bear Researcher": "空头研究员",
        "Research Manager": "研究管理",
        "Trader": "交易员",
        "Aggressive": "激进型风险分析师",
        "Conservative": "保守型风险分析师",
        "Neutral": "中立型风险分析师",
        "Portfolio Manager": "投资组合管理",
        "tools_market": "获取市场数据",
        "tools_social": "获取社交媒体数据",
        "tools_news": "获取新闻数据",
        "tools_fundamentals": "获取基本面数据",
        "Msg Clear Market": "清理市场消息",
        "Msg Clear Social": "清理社交媒体消息",
        "Msg Clear News": "清理新闻消息",
        "Msg Clear Fundamentals": "清理基本面消息",
    }
    return name_map.get(node_name, node_name)


# ============================================================================
# 分析执行线程
# ============================================================================

def run_analysis_sync(request: AnalyzeRequest):
    global analysis_status

    try:
        analysis_status = {
            "running": True,
            "company": request.company,
            "date": request.date,
            "start_time": datetime.now().isoformat(),
        }

        broadcast_threadsafe({
            "type": "analysis_start",
            "data": {
                "company": request.company,
                "date": request.date,
            },
        })

        config = DEFAULT_CONFIG.copy()
        config["llm_provider"] = request.llm_provider
        config["deep_think_llm"] = request.deep_think_llm
        config["quick_think_llm"] = request.quick_think_llm
        config["max_debate_rounds"] = request.max_debate_rounds
        config["max_risk_discuss_rounds"] = request.max_risk_discuss_rounds

        broadcast_threadsafe({
            "type": "progress",
            "data": {
                "node": "初始化",
                "message": "正在初始化 TradingAgents 多智能体系统...",
            },
        })

        ta = TradingAgentsGraph(debug=True, config=config)

        init_agent_state = ta.propagator.create_initial_state(
            request.company, request.date
        )
        args = ta.propagator.get_graph_args()

        trace = []
        last_node = ""

        for chunk in ta.graph.stream(init_agent_state, **args):
            trace.append(chunk)

            if "messages" not in chunk or len(chunk["messages"]) == 0:
                continue

            last_msg = chunk["messages"][-1]
            node_name = extract_node_name(chunk)
            display_name = node_display_name(node_name)

            if node_name != last_node and node_name != "Processing":
                last_node = node_name
                broadcast_threadsafe({
                    "type": "node_change",
                    "data": {
                        "node": node_name,
                        "display": display_name,
                    },
                })

            content = format_message_content(last_msg)

            broadcast_threadsafe({
                "type": "message",
                "data": {
                    "node": node_name,
                    "display": display_name,
                    "content": content,
                    "msg_type": type(last_msg).__name__,
                },
            })

        final_state = trace[-1] if trace else {}
        decision = ta.process_signal(
            final_state.get("final_trade_decision", "无法确定")
        )

        reports = {
            "market_report": final_state.get("market_report", ""),
            "sentiment_report": final_state.get("sentiment_report", ""),
            "news_report": final_state.get("news_report", ""),
            "fundamentals_report": final_state.get("fundamentals_report", ""),
            "investment_plan": final_state.get("trader_investment_plan", ""),
            "final_decision": final_state.get("final_trade_decision", ""),
        }

        broadcast_threadsafe({
            "type": "analysis_complete",
            "data": {
                "decision": decision,
                "decision_text": final_state.get("final_trade_decision", ""),
                "reports": reports,
                "company": request.company,
                "date": request.date,
            },
        })

    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        traceback_str = traceback.format_exc()
        broadcast_threadsafe({
            "type": "error",
            "data": {
                "message": error_msg,
                "traceback": traceback_str,
            },
        })
    finally:
        analysis_status["running"] = False
        broadcast_threadsafe({
            "type": "status",
            "data": analysis_status,
        })


# ============================================================================
# REST API 端点
# ============================================================================

@app.post("/api/analyze")
async def start_analysis(request: AnalyzeRequest):
    if analysis_status["running"]:
        return {
            "success": False,
            "message": "已有分析任务正在运行，请等待完成后再试",
        }

    thread = threading.Thread(
        target=run_analysis_sync,
        args=(request,),
        daemon=True,
    )
    thread.start()

    return {
        "success": True,
        "message": f"分析任务已启动: {request.company} ({request.date})",
    }


@app.get("/api/status")
async def get_status():
    return analysis_status


# ============================================================================
# 静态文件服务
# ============================================================================

frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/static", StaticFiles(directory=frontend_dir), name="static")


@app.get("/")
async def serve_frontend():
    index_path = os.path.join(
        os.path.dirname(__file__), "..", "frontend", "index.html"
    )
    return FileResponse(index_path)