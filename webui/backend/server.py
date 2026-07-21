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

# 分析任务取消事件：run_analysis_sync 在每次 graph.stream 迭代前检查此标志，
# 若被设置则提前退出循环，实现优雅终止（而非 kill 线程）
# 由 /api/stop 端点设置，由 run_analysis_sync 在 finally 中清除
analysis_cancel_event = threading.Event()


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

        # 完整 reports 字段：保持与 AgentState 字段名一致，前端按 key 渲染对应卡片
        # 注意：investment_plan 是研究经理的推荐，trader_investment_plan 是交易员的执行方案
        reports = {
            "market_report": final_state.get("market_report", ""),
            "sentiment_report": final_state.get("sentiment_report", ""),
            "news_report": final_state.get("news_report", ""),
            "fundamentals_report": final_state.get("fundamentals_report", ""),
            "investment_plan": final_state.get("investment_plan", ""),
            "trader_investment_plan": final_state.get("trader_investment_plan", ""),
            "final_trade_decision": final_state.get("final_trade_decision", ""),
        }

        broadcast_threadsafe({
            "type": "analysis_complete",
            "data": {
                "decision": decision,
                "decision_text": final_state.get("final_trade_decision", ""),
                "reports": reports,
                "company": request.company,
                "date": request.date,
                "cancelled": analysis_cancel_event.is_set(),
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
        was_cancelled = analysis_cancel_event.is_set()
        analysis_status["running"] = False
        analysis_status["cancelled"] = was_cancelled
        # 清除取消标志，为下次分析做准备
        analysis_cancel_event.clear()
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


@app.post("/api/stop")
async def stop_analysis():
    """
    请求停止当前正在运行的分析任务。
    设置取消事件标志，run_analysis_sync 在下一次 graph.stream 迭代时检测到后优雅退出。
    注意：这是协作式取消，不能立即中断正在进行的 LLM 调用或工具调用，
    只能在节点之间切换时生效。
    """
    if not analysis_status["running"]:
        return {"success": False, "message": "当前没有正在运行的分析任务"}
    analysis_cancel_event.set()
    return {"success": True, "message": "已发送停止信号，任务将在当前节点完成后终止"}


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