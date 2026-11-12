"""A 股资金流向数据工具。

提供个股主力资金、超大单、大单等资金流向数据，用于判断资金面强弱。
数据来源为东方财富（通过 AkShare），仅支持 A 股标的。
"""

from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.interface import route_to_vendor


@tool
def get_capital_flow(
    ticker: Annotated[str, "A 股股票代码，如 600519、000001、830799"],
    curr_date: Annotated[
        str, "当前交易日期，格式 yyyy-mm-dd；不传则返回最近数据"
    ] = None,
    look_back_days: Annotated[int, "回溯交易日数量，默认 20"] = 20,
) -> str:
    """获取 A 股个股资金流向数据（主力资金 / 超大单 / 大单 / 中单 / 小单）。

    资金面是 A 股短线择时的重要参考：
    - 主力净流入为正且持续放大，通常代表资金关注度提升；
    - 主力持续净流出、大单净卖出，往往对应资金撤离压力；
    - 结合价格位置与成交量判断，避免仅凭单日资金流向做决策。

    参数：
        ticker (str): A 股代码，如 "600519"
        curr_date (str): 当前交易日，格式 yyyy-mm-dd
        look_back_days (int): 回溯交易日数量，默认 20

    返回：
        str: 含最近若干交易日资金流向明细与主力净流入汇总的文本
    """
    try:
        return route_to_vendor(
            "get_capital_flow", ticker, curr_date, look_back_days
        )
    except Exception as exc:  # 避免单个数据源异常中断整个分析流程
        return f"资金流向数据暂时不可用（{type(exc).__name__}: {exc}）"


@tool
def get_northbound_flow(
    curr_date: Annotated[
        str, "当前交易日，格式 yyyy-mm-dd；不传则返回最近数据"
    ] = None,
    look_back_days: Annotated[int, "回溯交易日数量，默认 10"] = 10,
) -> str:
    """获取北向资金（沪深股通）净流入数据，用于判断外资对 A 股的整体态度。

    注意：交易所自 2024-08-19 起不再每日披露北向资金净买入额，
    工具输出会标注数据截止日期；若数据明显滞后，请勿据此判断当前资金动向。

    参数：
        curr_date (str): 当前交易日，格式 yyyy-mm-dd
        look_back_days (int): 回溯交易日数量，默认 10

    返回：
        str: 北向资金最近若干交易日的净流入明细与汇总
    """
    try:
        return route_to_vendor(
            "get_northbound_flow", curr_date, look_back_days
        )
    except Exception as exc:
        return f"北向资金数据暂时不可用（{type(exc).__name__}: {exc}）"


@tool
def get_dragon_tiger(
    ticker: Annotated[str, "A 股股票代码，如 600519、000001"],
    curr_date: Annotated[
        str, "当前交易日，格式 yyyy-mm-dd；不传则返回最近数据"
    ] = None,
    look_back_days: Annotated[int, "回溯自然日数量，默认 60"] = 60,
) -> str:
    """获取 A 股个股龙虎榜数据（上榜原因、买卖金额、席位解读等）。

    龙虎榜反映异动个股的资金博弈情况：
    - 净买入额较大通常代表游资/机构关注度提升；
    - 净卖出额较大且上榜原因偏负面时需警惕资金撤离；
    - 上榜后 1/5/10 日表现可辅助判断短线持续性。

    参数：
        ticker (str): A 股代码
        curr_date (str): 当前交易日，格式 yyyy-mm-dd
        look_back_days (int): 回溯自然日数量，默认 60

    返回：
        str: 该股票最近龙虎榜记录与净买额汇总
    """
    try:
        return route_to_vendor(
            "get_dragon_tiger", ticker, curr_date, look_back_days
        )
    except Exception as exc:
        return f"龙虎榜数据暂时不可用（{type(exc).__name__}: {exc}）"
