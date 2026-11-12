"""A 股事件数据模块离线测试（mock AkShare，全程不触网）。"""

import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from tradingagents.dataflows import ashare_events


def _passthrough(func, **kwargs):
    """测试中跳过重试与限流等待，直接执行一次。"""
    return func()


class EventsTestBase(unittest.TestCase):
    def setUp(self):
        ashare_events.clear_caches()
        retry_patcher = patch.object(
            ashare_events, "retry_with_backoff", side_effect=_passthrough
        )
        retry_patcher.start()
        self.addCleanup(retry_patcher.stop)

    def use_ak(self, **members):
        fake = SimpleNamespace(**members)
        patcher = patch.object(ashare_events, "ak", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake


class ValuationPercentileTests(EventsTestBase):
    def test_known_percentile_for_pe_and_pb(self):
        dates = pd.date_range("2025-01-01", periods=100, freq="D")
        pe = pd.DataFrame({"date": dates, "value": range(1, 101)})
        pb = pd.DataFrame({"date": dates, "value": range(100, 0, -1)})

        def fake_valuation(symbol, indicator, period):
            return pe if indicator == "市盈率(TTM)" else pb

        self.use_ak(stock_zh_valuation_baidu=fake_valuation)
        result = ashare_events.get_valuation_percentile(
            "600519", curr_date="2025-04-10", years=5
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["ticker"], "600519")
        self.assertEqual(result["samples"], 100)
        self.assertEqual(result["pe_ttm"]["value"], 100.0)
        self.assertEqual(result["pe_ttm"]["percentile"], 100.0)
        self.assertEqual(result["pe_ttm"]["median"], 50.5)
        self.assertEqual(result["pb"]["value"], 1.0)
        self.assertEqual(result["pb"]["percentile"], 1.0)
        self.assertEqual(result["level"], "高估")

    def test_too_few_samples_fails_cleanly(self):
        dates = pd.date_range("2025-01-01", periods=10, freq="D")
        frame = pd.DataFrame({"date": dates, "value": range(1, 11)})

        self.use_ak(stock_zh_valuation_baidu=lambda **kwargs: frame)
        result = ashare_events.get_valuation_percentile(
            "600519", curr_date="2025-01-10", years=5
        )

        self.assertFalse(result["success"])
        self.assertTrue(any("样本不足" in item for item in result["warnings"]))
        self.assertIsNone(result["pe_ttm"])
        self.assertIsNone(result["pb"])

    def test_curr_date_excludes_future_samples(self):
        dates = pd.date_range("2024-01-01", periods=100, freq="D")
        history = pd.DataFrame({"date": dates, "value": range(1, 101)})
        future = pd.DataFrame(
            {"date": pd.to_datetime(["2025-05-01"]), "value": [9999.0]}
        )
        frame = pd.concat([history, future], ignore_index=True)

        self.use_ak(stock_zh_valuation_baidu=lambda **kwargs: frame)
        result = ashare_events.get_valuation_percentile(
            "600519", curr_date="2024-04-09", years=5
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["samples"], 100)
        self.assertEqual(result["pe_ttm"]["max"], 100.0)
        self.assertEqual(result["pe_ttm"]["value"], 100.0)
        self.assertEqual(result["as_of"], "2024-04-09")


class EarningsForecastTests(EventsTestBase):
    def _frames(self):
        latest = ashare_events._recent_report_periods(date.today(), 6)[0]
        yjyg = pd.DataFrame(
            [
                {
                    "股票代码": "600519",
                    "股票简称": "贵州茅台",
                    "预测指标": "净利润",
                    "业绩变动": "预计同比增长",
                    "业绩变动幅度": 15.2,
                    "预测数值": 123456,
                    "预告类型": "预增",
                    "业绩变动原因": "产品结构优化",
                    "公告日期": "2026-01-20",
                },
                {
                    "股票代码": 1,
                    "股票简称": "平安银行",
                    "预测指标": "净利润",
                    "业绩变动幅度": -5.0,
                    "公告日期": "2026-01-21",
                },
                {
                    "股票代码": "000001",
                    "股票简称": "平安银行",
                    "预测指标": "营业收入",
                    "业绩变动幅度": 3.0,
                    "公告日期": "2026-01-21",
                },
            ]
        )
        yjkb = pd.DataFrame(
            columns=[
                "股票代码",
                "股票简称",
                "净利润-净利润",
                "净利润-同比增长",
                "公告日期",
            ]
        )
        return latest, yjyg, yjkb

    def test_filters_by_code_and_returns_latest(self):
        latest, yjyg, yjkb = self._frames()
        latest_ak = latest.strftime("%Y%m%d")

        def fake_yjyg(date=None, **kwargs):
            return yjyg if date == latest_ak else pd.DataFrame()

        def fake_yjkb(date=None, **kwargs):
            return yjkb if date == latest_ak else pd.DataFrame()

        self.use_ak(stock_yjyg_em=fake_yjyg, stock_yjkb_em=fake_yjkb)
        result = ashare_events.get_earnings_forecast("600519")

        self.assertTrue(result["success"])
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["latest"]["indicator"], "净利润")
        self.assertEqual(result["latest"]["change_pct"], 15.2)
        self.assertEqual(result["latest"]["announce_date"], "2026-01-20")
        self.assertEqual(result["latest"]["report_period"], latest.isoformat())
        self.assertIn("净利润", result["summary"])

    def test_handles_numeric_code_with_zfill(self):
        latest, yjyg, yjkb = self._frames()
        latest_ak = latest.strftime("%Y%m%d")

        self.use_ak(
            stock_yjyg_em=lambda date=None, **kwargs: (
                yjyg if date == latest_ak else pd.DataFrame()
            ),
            stock_yjkb_em=lambda date=None, **kwargs: yjkb,
        )
        result = ashare_events.get_earnings_forecast("000001")

        self.assertTrue(result["success"])
        self.assertEqual(result["ticker"], "000001")
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(
            {item["indicator"] for item in result["items"]},
            {"净利润", "营业收入"},
        )
        self.assertIsNotNone(result["latest"])


class DisclosureCalendarTests(EventsTestBase):
    def _frame(self):
        return pd.DataFrame(
            [
                {
                    "股票代码": "600519",
                    "股票简称": "贵州茅台",
                    "首次预约": "2026-10-30",
                    "初次变更": pd.NaT,
                    "二次变更": pd.NaT,
                    "三次变更": pd.NaT,
                    "实际披露": "2026-10-29",
                },
                {
                    "股票代码": "000001",
                    "股票简称": "平安银行",
                    "首次预约": "2026-10-24",
                    "初次变更": pd.NaT,
                    "二次变更": pd.NaT,
                    "三次变更": pd.NaT,
                    "实际披露": pd.NaT,
                },
            ]
        )

    def test_returns_item_and_summary(self):
        frame = self._frame()
        self.use_ak(stock_report_disclosure=lambda market, period: frame)
        result = ashare_events.get_report_disclosure_calendar(
            "600519", period="2026三季"
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["period"], "2026三季")
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["ticker"], "600519")
        self.assertEqual(result["items"][0]["disclose_date"], "2026-10-30")
        self.assertEqual(result["items"][0]["actual_date"], "2026-10-29")
        self.assertTrue(result["summary"])

    def test_normalizes_half_year_alias(self):
        frame = self._frame()
        calls = []

        def fake(market, period):
            calls.append(period)
            return frame

        self.use_ak(stock_report_disclosure=fake)
        result = ashare_events.get_report_disclosure_calendar(
            "600519", period="2026半年"
        )

        self.assertTrue(result["success"])
        self.assertEqual(calls, ["2026半年报"])
        self.assertEqual(result["period"], "2026半年报")


class LockupExpiryTests(EventsTestBase):
    def test_filters_window_and_flags_near_release(self):
        today = date(2026, 11, 12)
        frame = pd.DataFrame(
            [
                {
                    "解禁时间": "2026-11-20",
                    "解禁数量": 1200000.0,
                    "占总市值比例": 0.0025,
                    "实际解禁数量市值": 180000000.0,
                    "限售股类型": "定向增发机构配售股份",
                },
                {
                    "解禁时间": "2027-08-01",
                    "解禁数量": 500.0,
                    "占总市值比例": 0.1,
                    "实际解禁数量市值": 1000.0,
                    "限售股类型": "首发原股东限售股份",
                },
            ]
        )
        self.use_ak(stock_restricted_release_queue_em=lambda symbol: frame)
        result = ashare_events.get_lockup_expiry(
            "600519", months=6, today=today
        )

        self.assertTrue(result["success"])
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["release_date"], "2026-11-20")
        self.assertEqual(result["items"][0]["shares"], 1200000.0)
        self.assertEqual(result["nearest_days"], 8)
        self.assertEqual(result["level"], "warn")
        self.assertIn("8 天后", result["summary"])

    def test_far_release_is_info_level(self):
        today = date(2026, 11, 12)
        frame = pd.DataFrame(
            [
                {
                    "解禁时间": "2026-12-20",
                    "解禁数量": 100.0,
                    "占总市值比例": 0.001,
                    "限售股类型": "股权激励限售股份",
                }
            ]
        )
        self.use_ak(stock_restricted_release_queue_em=lambda symbol: frame)
        result = ashare_events.get_lockup_expiry(
            "600519", months=6, today=today
        )

        self.assertEqual(result["nearest_days"], 38)
        self.assertEqual(result["level"], "info")


class DividendScheduleTests(EventsTestBase):
    def test_parses_ex_date_and_plan(self):
        today = date(2026, 11, 12)
        frame = pd.DataFrame(
            [
                {
                    "代码": "600519",
                    "名称": "贵州茅台",
                    "除权除息日": "2026-12-05",
                    "股权登记日": "2026-12-04",
                    "现金分红-现金分红比例": 2.0,
                    "送转股份-送转总比例": 0.0,
                    "方案进度": "实施分配",
                },
                {
                    "代码": "600519",
                    "名称": "贵州茅台",
                    "除权除息日": pd.NaT,
                    "股权登记日": pd.NaT,
                    "现金分红-现金分红比例": 1.0,
                    "送转股份-送转总比例": 0.0,
                    "方案进度": "预案",
                },
            ]
        )

        def fake_fhps(date=None, **kwargs):
            if date == "20251231":
                return frame
            return pd.DataFrame(columns=frame.columns)

        self.use_ak(stock_fhps_em=fake_fhps)
        result = ashare_events.get_dividend_schedule(
            "600519", days=90, today=today
        )

        self.assertTrue(result["success"])
        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertEqual(item["ex_date"], "2026-12-05")
        self.assertEqual(item["record_date"], "2026-12-04")
        self.assertEqual(item["cash_per_share"], 2.0)
        self.assertEqual(item["progress"], "实施分配")
        self.assertIn("除权除息", result["summary"])


class FinancialRedFlagTests(EventsTestBase):
    def _abstract_frame(self, metrics, report="20250630"):
        return pd.DataFrame(
            [
                {"选项": "常用指标", "指标": name, report: value}
                for name, value in metrics.items()
            ]
        )

    def _balance_frame(
        self,
        goodwill=0.0,
        equity=1e10,
        accounts_rece=1e9,
        accounts_rece_yoy=10.0,
        report="2025-06-30",
        notice="2025-08-31",
    ):
        return pd.DataFrame(
            [
                {
                    "REPORT_DATE": report,
                    "NOTICE_DATE": notice,
                    "GOODWILL": goodwill,
                    "TOTAL_EQUITY": equity,
                    "ACCOUNTS_RECE": accounts_rece,
                    "ACCOUNTS_RECE_YOY": accounts_rece_yoy,
                }
            ]
        )

    def _pledge_frame(self, ratio=10.0):
        return pd.DataFrame(
            [{"股票代码": "600519", "股票简称": "贵州茅台", "质押比例": ratio}]
        )

    def run_flags(
        self,
        goodwill=0.0,
        equity=1e10,
        accounts_rece=1e9,
        accounts_rece_yoy=10.0,
        pledge_ratio=10.0,
        revenue=1e10,
        net_profit=1e9,
        ocf=8e8,
        revenue_yoy=10.0,
    ):
        metrics = {
            "商誉": "nan",
            "股东权益合计(净资产)": str(equity),
            "营业总收入": str(revenue),
            "归母净利润": str(net_profit),
            "经营现金流量净额": str(ocf),
            "营业总收入增长率": str(revenue_yoy),
        }
        abstract = self._abstract_frame(metrics)
        balance = self._balance_frame(
            goodwill, equity, accounts_rece, accounts_rece_yoy
        )
        pledge = self._pledge_frame(pledge_ratio)
        self.use_ak(
            stock_financial_abstract=lambda symbol: abstract,
            stock_balance_sheet_by_report_em=lambda symbol: balance,
            stock_gpzy_pledge_ratio_em=lambda date: pledge,
        )
        return ashare_events.get_financial_red_flags(
            "600519", curr_date="2025-12-31"
        )

    @staticmethod
    def check_of(result, code):
        return next(item for item in result["checks"] if item["code"] == code)

    def test_goodwill_thresholds(self):
        cases = [(0.0, "ok"), (1e9, "warn"), (3e9, "danger")]
        for goodwill, expected in cases:
            with self.subTest(goodwill=goodwill):
                ashare_events.clear_caches()
                result = self.run_flags(goodwill=goodwill)
                check = self.check_of(result, "goodwill")
                self.assertEqual(check["level"], expected)
                self.assertAlmostEqual(check["value"], goodwill / 1e10 * 100)

    def test_pledge_thresholds(self):
        cases = [(29.9, "ok"), (30.0, "warn"), (50.0, "danger")]
        for ratio, expected in cases:
            with self.subTest(ratio=ratio):
                ashare_events.clear_caches()
                result = self.run_flags(pledge_ratio=ratio)
                check = self.check_of(result, "pledge")
                self.assertEqual(check["level"], expected)
                self.assertEqual(check["value"], ratio)

    def test_receivables_thresholds(self):
        cases = [(4e9, "ok"), (5e9, "warn"), (1e10, "danger")]
        for accounts_rece, expected in cases:
            with self.subTest(accounts_rece=accounts_rece):
                ashare_events.clear_caches()
                result = self.run_flags(accounts_rece=accounts_rece)
                check = self.check_of(result, "receivables")
                self.assertEqual(check["level"], expected)
                self.assertAlmostEqual(check["value"], accounts_rece / 1e10 * 100)

    def test_receivables_growth_outpacing_revenue_warns(self):
        result = self.run_flags(accounts_rece=1e9, accounts_rece_yoy=50.0)
        check = self.check_of(result, "receivables")
        self.assertEqual(check["level"], "warn")
        self.assertIn("超过 30 个百分点", check["message"])

    def test_operating_cashflow_thresholds(self):
        cases = [(-1e8, "danger"), (4e8, "warn"), (5e8, "ok"), (2e9, "ok")]
        for ocf, expected in cases:
            with self.subTest(ocf=ocf):
                ashare_events.clear_caches()
                result = self.run_flags(ocf=ocf)
                check = self.check_of(result, "operating_cashflow")
                self.assertEqual(check["level"], expected)
                if expected == "danger":
                    self.assertIn("利润未转化为现金", check["message"])

    def test_missing_sources_degrade_without_fabrication(self):
        def boom(**kwargs):
            raise RuntimeError("network down")

        self.use_ak(
            stock_financial_abstract=boom,
            stock_balance_sheet_by_report_em=boom,
            stock_gpzy_pledge_ratio_em=boom,
        )
        result = ashare_events.get_financial_red_flags(
            "600519", curr_date="2025-12-31"
        )

        self.assertTrue(result["success"])
        self.assertEqual(len(result["checks"]), 4)
        self.assertTrue(result["warnings"])
        for check in result["checks"]:
            self.assertEqual(check["level"], "ok")
            self.assertIsNone(check["value"])
            self.assertEqual(check["message"], "数据不足，无法判断")
        self.assertEqual(result["risk_score"], 0)
        self.assertEqual(result["level"], "低风险")

    # --------------------------------------------------------------
    # 回归：两处实测中发现的误报 / 误判
    # --------------------------------------------------------------

    def test_goodwill_nan_with_valid_balance_sheet_counts_as_zero(self):
        """资产负债表存在、净资产有效、商誉字段为空 → 视为"无商誉"。

        WHY：A 股财报中无商誉的公司该字段通常为空而不是 0。此前会被判成
        "数据不足"，于是茅台这类公司反而拿不到该项结论。
        """
        abstract = self._abstract_frame(
            {"营业收入": "1e10", "归母净利润": "1e9", "经营现金流量净额": "8e8"}
        )
        balance = self._balance_frame(goodwill=float("nan"))
        pledge = self._pledge_frame(ratio=0.1)
        self.use_ak(
            stock_financial_abstract=lambda symbol: abstract,
            stock_balance_sheet_by_report_em=lambda symbol: balance,
            stock_gpzy_pledge_ratio_em=lambda date: pledge,
        )

        result = ashare_events.get_financial_red_flags(
            "600519", curr_date="2025-12-31"
        )
        check = self.check_of(result, "goodwill")
        self.assertEqual(check["level"], "ok")
        self.assertEqual(check["value"], 0.0)
        self.assertNotEqual(check["message"], "数据不足，无法判断")

    def test_small_receivables_base_does_not_trigger_trend_warning(self):
        """应收账款占营收极小时，同比增速再高也不应告警。

        WHY：0.3% 的应收账款哪怕同比 +1300%，也只占营收 4% ——
        小基数放大的增速没有统计意义，不能据此判定回款质量恶化。
        """
        result = self.run_flags(
            accounts_rece=3e7,          # 营收 1e10 的 0.3%
            accounts_rece_yoy=1300.0,   # 同比爆炸式增长
            revenue=1e10,
            revenue_yoy=2.0,
        )
        check = self.check_of(result, "receivables")
        self.assertEqual(check["level"], "ok")
        self.assertNotIn("快于营收同比", check["message"])

    def test_meaningful_receivables_base_still_warns_on_trend(self):
        """应收账款占比达到有意义水平时，增速背离仍应告警。"""
        result = self.run_flags(
            accounts_rece=4e9,          # 营收 1e10 的 40%
            accounts_rece_yoy=120.0,
            revenue=1e10,
            revenue_yoy=5.0,
        )
        check = self.check_of(result, "receivables")
        self.assertIn(check["level"], ("warn", "danger"))
        self.assertIn("快于营收同比", check["message"])


class EventCalendarTests(EventsTestBase):
    def test_merges_event_types_and_sorts_by_date(self):
        today = date(2026, 11, 12)
        lockup = {
            "success": True,
            "warnings": [],
            "items": [
                {
                    "release_date": "2026-12-01",
                    "shares": 1200000.0,
                    "ratio": 0.0025,
                    "ratio_name": "占总市值比例",
                    "share_type": "定向增发机构配售股份",
                }
            ],
        }
        disclosure = {
            "success": True,
            "period": "2026年报",
            "warnings": [],
            "items": [
                {
                    "disclose_date": "2026-11-25",
                    "actual_date": "",
                    "change_reason": "",
                }
            ],
        }
        dividend = {
            "success": True,
            "warnings": [],
            "items": [
                {
                    "ex_date": "2026-11-20",
                    "record_date": "2026-11-19",
                    "cash_per_share": 2.0,
                    "progress": "实施分配",
                }
            ],
        }
        forecast = {
            "success": True,
            "latest": None,
            "summary": "",
            "warnings": [],
        }

        with (
            patch.object(
                ashare_events, "get_lockup_expiry", return_value=lockup
            ),
            patch.object(
                ashare_events,
                "get_report_disclosure_calendar",
                return_value=disclosure,
            ),
            patch.object(
                ashare_events, "get_dividend_schedule", return_value=dividend
            ),
            patch.object(
                ashare_events, "get_earnings_forecast", return_value=forecast
            ),
        ):
            result = ashare_events.build_event_calendar(
                "600519", days=120, today=today
            )

        self.assertTrue(result["success"])
        events = result["events"]
        self.assertEqual(
            {event["type"] for event in events},
            {"解禁", "财报披露", "除权除息", "业绩预告"},
        )
        dates = [event["date"] for event in events]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(result["counts"]["解禁"], 1)
        self.assertEqual(result["counts"]["财报披露"], 1)
        self.assertEqual(result["counts"]["除权除息"], 1)
        self.assertEqual(result["counts"]["业绩预告"], 1)
        self.assertEqual(sum(result["counts"].values()), len(events))
        self.assertIn("未来 120 天有 4 个事件", result["summary"])


class NonAShareRejectionTests(EventsTestBase):
    def test_all_entrypoints_reject_non_a_share_without_akshare(self):
        class ExplodingAk:
            def __getattr__(self, name):
                raise AssertionError(f"不应访问 AkShare: {name}")

        with patch.object(ashare_events, "ak", ExplodingAk()):
            calls = [
                lambda: ashare_events.get_valuation_percentile("NVDA"),
                lambda: ashare_events.get_earnings_forecast("NVDA"),
                lambda: ashare_events.get_report_disclosure_calendar("NVDA"),
                lambda: ashare_events.get_lockup_expiry("NVDA"),
                lambda: ashare_events.get_dividend_schedule("NVDA"),
                lambda: ashare_events.get_financial_red_flags("NVDA"),
                lambda: ashare_events.build_event_calendar("NVDA"),
            ]
            for call in calls:
                result = call()
                self.assertFalse(result["success"])
                self.assertEqual(result["ticker"], "NVDA")
                self.assertIn("A 股", result["summary"])
                self.assertTrue(
                    any("AkShare" in warning for warning in result["warnings"])
                )


if __name__ == "__main__":
    unittest.main()


