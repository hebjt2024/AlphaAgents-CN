"""财务报表数据源回退：THS 接口被 AkShare 移除后仍能出数。"""

from unittest.mock import patch

import pandas as pd

from tradingagents.dataflows import akshare_adapter


def test_financial_statement_falls_back_to_sina():
    frame = pd.DataFrame(
        [
            {"报告期": "2026-09-30", "资产总计": 100.0, "货币资金": 10.0},
            {"报告期": "2026-06-30", "资产总计": 95.0, "货币资金": 9.0},
        ]
    )
    with patch.object(
        akshare_adapter.ak,
        "stock_financial_report_sina",
        return_value=frame,
        create=True,
    ):
        text = akshare_adapter._fetch_financial_statement_ths(
            code="000878",
            ths_func=None,
            statement_name="Balance Sheet",
            freq="quarterly",
            curr_date="2026-11-17",
            sina_indicator="资产负债表",
        )
    assert "# Balance Sheet data for 000878" in text
    assert "资产总计" in text
    assert "2026-09-30" in text
