"""用仿 yfinance 返回格式的假对象测试取数逻辑 (不联网)。
假对象的形状按 yfinance 1.7 源码构造: 财报列为日期降序, 行是 Yahoo 的标签;
eps_trend 的 index 为 0q/+1q/0y/+1y, 列为 current/30daysAgo/...。"""
import numpy as np
import pandas as pd
import pytest

from quant_brain.value_factor import fetch as F
from quant_brain.value_factor.factors import screen


def _stmt(rows: dict, years=(2024, 2023, 2022, 2021)):
    cols = [pd.Timestamp(f"{y}-09-30") for y in years]
    return pd.DataFrame({c: {k: v[i] for k, v in rows.items()} for i, c in enumerate(cols)})


class FakeTicker:
    calls = 0

    def __init__(self, sym, drop=(), fail_first=0, no_est=False, days=300):
        self.sym, self.drop, self.fail_first, self.no_est, self.days = sym, set(drop), fail_first, no_est, days
        self.info = {"currentPrice": 120.0, "sharesOutstanding": 1e9, "beta": 1.1, "sector": "Technology",
                     "nextFiscalYearEnd": int((pd.Timestamp.today() + pd.Timedelta(days=182)).timestamp())}

    def _chk(self):
        FakeTicker.calls += 1
        if FakeTicker.calls <= self.fail_first:
            raise ConnectionError("rate limited")

    @property
    def income_stmt(self):
        self._chk()
        r = {"Total Revenue": [100e9, 95e9, 90e9, 80e9], "Net Income": [25e9, 23e9, 21e9, 18e9],
             "EBIT": [32e9, 30e9, 27e9, 23e9]}
        return _stmt({k: v for k, v in r.items() if k not in self.drop})

    @property
    def cashflow(self):
        r = {"Operating Cash Flow": [30e9, 28e9, 25e9, 20e9], "Capital Expenditure": [-5e9, -5e9, -4e9, -4e9]}
        return _stmt({k: v for k, v in r.items() if k not in self.drop})

    @property
    def balance_sheet(self):
        r = {"Total Debt": [20e9] * 4, "Cash Cash Equivalents And Short Term Investments": [30e9] * 4,
             "Stockholders Equity": [60e9] * 4}
        return _stmt(r)

    def history(self, period="2y", auto_adjust=False):
        idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=self.days).tz_localize("America/New_York")
        return pd.DataFrame({"Close": np.linspace(90, 120, self.days)}, index=idx)

    @property
    def eps_trend(self):
        if self.no_est:
            raise KeyError("epsTrend")
        return pd.DataFrame({"current": [1.5, 1.6, 6.0, 7.0], "30daysAgo": [1.5, 1.6, 5.9, 6.8],
                             "60daysAgo": [1.4, 1.5, 5.8, 6.6], "90daysAgo": [1.4, 1.5, 5.6, 6.4]},
                            index=pd.Index(["0q", "+1q", "0y", "+1y"], name="period"))

    @property
    def eps_revisions(self):
        return pd.DataFrame({"upLast30days": [3, 4, 9, 8], "downLast30days": [0, 1, 1, 2]},
                            index=pd.Index(["0q", "+1q", "0y", "+1y"], name="period"))


def _fetch(tmp_path, **kw):
    return F.fetch_stock("TEST", cache_dir=str(tmp_path), backoff=0, factory=lambda s: FakeTicker(s, **kw))


def test_parses_yfinance_shapes(tmp_path):
    s, rep = _fetch(tmp_path)
    assert rep.ok and s.fin.index.tolist() == [2021, 2022, 2023, 2024]     # 升序
    assert s.fin["capex"].iloc[-1] == -5e9 and s.fin["cash"].iloc[-1] == 30e9
    assert s.prices.index.tz is None
    # 财年还剩约一半: 前瞻EPS应在 0y(6.0) 与 +1y(7.0) 之间, 约 6.5
    assert 6.4 < s.eps_fwd < 6.6
    # 90 天前同口径 ≈ 6.0, 修正约 +8%
    assert s.eps_fwd / s.eps_fwd_90d - 1 == pytest.approx(0.083, abs=0.01)
    assert (s.rev_up_30d, s.rev_down_30d) == (9, 1)


def test_feeds_screener(tmp_path):
    s, _ = _fetch(tmp_path)
    t = screen({"TEST": s})
    assert t.loc["TEST", "fwd_pe"] == pytest.approx(120 / s.eps_fwd)


def test_missing_required_field_skips_ticker(tmp_path):
    s, rep = _fetch(tmp_path, drop=["Operating Cash Flow"])
    assert s is None and not rep.ok and "cfo" in rep.warnings[-1]


def test_no_analyst_coverage_warns_not_fails(tmp_path):
    s, rep = _fetch(tmp_path, no_est=True)
    assert rep.ok and np.isnan(s.eps_fwd) and any("无盈利预期" in w for w in rep.warnings)
    assert not screen({"TEST": s}).loc["TEST", "pass_gates"]


def test_retry_on_network_error(tmp_path):
    FakeTicker.calls = 0
    s, rep = _fetch(tmp_path, fail_first=2)
    assert s is not None and rep.ok


def test_gives_up_after_retries(tmp_path):
    FakeTicker.calls = 0
    s, rep = _fetch(tmp_path, fail_first=99)
    assert s is None and "ConnectionError" in rep.warnings[-1]


def test_short_history_rejected(tmp_path):
    s, rep = _fetch(tmp_path, days=100)
    assert s is None and "价格历史" in rep.warnings[-1]


def test_cache_roundtrip(tmp_path):
    _fetch(tmp_path)
    FakeTicker.calls = 0
    s, rep = F.fetch_stock("TEST", cache_dir=str(tmp_path), factory=lambda s: (_ for _ in ()).throw(AssertionError("不该联网")))
    assert rep.from_cache and s.eps_fwd == pytest.approx(6.5, abs=0.1)


def test_financial_sector_excluded(tmp_path):
    s, _ = _fetch(tmp_path)
    s.sector = "Financial Services"
    assert "不适用" in screen({"TEST": s}).loc["TEST", "fail_reasons"]


def test_negative_ebit_not_waved_through(tmp_path):
    s, _ = _fetch(tmp_path)
    s.fin.loc[s.fin.index[-1], "ebit"] = -1e9
    assert "EBIT<=0" in screen({"TEST": s}).loc["TEST", "fail_reasons"]
