"""行情文本 → 标准化 DataFrame。

``dataflows`` 返回的是 CSV 文本（带 ``#`` 注释头），列名可能是中文或英文，
日期也可能被当成索引。这里把清洗口径集中到一处，避免回测、评估、
本地缓存各写一套，出现"同一份数据两种结果"。
"""

from __future__ import annotations

from io import StringIO

import pandas as pd


def normalize_price_frame(text: str) -> pd.DataFrame:
    """把 dataflows 的 CSV 文本清洗成 ``Date/Open/High/Low/Close/Volume``。

    - 兼容中文列名与 Date 被当作索引；
    - 丢弃无效行、按日期升序、同日去重（保留最后一条）；
    - 只保留收盘价为正的记录。
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("未获取到行情数据")

    frame = pd.read_csv(StringIO(text), comment="#")
    frame = frame.rename(
        columns={
            "日期": "Date",
            "开盘": "Open",
            "最高": "High",
            "最低": "Low",
            "收盘": "Close",
            "成交量": "Volume",
            "成交额": "Amount",
        }
    )
    if "Date" not in frame.columns and frame.index.name in ("Date", "日期"):
        frame = frame.reset_index()
        if "日期" in frame.columns:
            frame = frame.rename(columns={"日期": "Date"})

    if "Date" not in frame.columns or "Close" not in frame.columns:
        raise ValueError("行情数据格式不符合预期")

    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    for column in ("Open", "High", "Low", "Close", "Volume", "Amount"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")

    frame = frame.dropna(subset=["Date", "Close"])
    frame = frame[frame["Close"] > 0]
    frame = (
        frame.sort_values("Date")
        .drop_duplicates(subset=["Date"], keep="last")
        .reset_index(drop=True)
    )
    if frame.empty:
        raise ValueError("行情数据为空")
    return frame


__all__ = ["normalize_price_frame"]
