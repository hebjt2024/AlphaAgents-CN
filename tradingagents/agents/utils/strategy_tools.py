"""策略扫描工具：把经典策略信号提供给智能体。"""

from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.config import get_config
from tradingagents.strategies import format_strategy_report, scan_all_strategies


@tool
def get_strategy_signals(
    ticker: Annotated[str, "A 股股票代码或名称，如 600519 / 贵州茅台"],
    curr_date: Annotated[
        str, "分析日期，格式 yyyy-mm-dd；不传则使用最新数据"
    ] = None,
    strategies: Annotated[
        str,
        "可选，逗号分隔的策略 key；不传则使用本次分析配置的策略集合",
    ] = None,
) -> str:
    """运行本次分析启用的经典策略扫描。

    未显式传入 strategies 时，使用 WebUI/配置中选中的策略集合，
    不会每次运行全部 15 个策略。
    """
    try:
        keys = None
        if strategies:
            keys = [item.strip() for item in strategies.split(",") if item.strip()]
        else:
            configured = get_config().get("strategy_keys")
            if configured:
                keys = list(configured)
        result = scan_all_strategies(ticker, curr_date, keys)
        return format_strategy_report(result)
    except Exception as exc:
        return f"策略扫描暂时不可用（{type(exc).__name__}: {exc}）"
