"""选股器的数据结构与数据源。

StockData 是单只股票的全部输入。数据源可替换:
  - load_json / save_json: 手工或其他渠道准备的数据 (离线、可复现)
  - fetch_yfinance: 从 Yahoo 拉取 (需要联网, 字段名随 yfinance 版本可能变化)
  - demo_basket: 随机生成的假数据, 只用来跑通流程, 不代表任何真实公司
"""
import json
from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np
import pandas as pd

FIN_COLS = ["revenue", "net_income", "ebit", "cfo", "capex", "total_debt", "cash", "equity"]


@dataclass
class StockData:
    ticker: str
    price: float
    shares: float
    beta: float
    fin: pd.DataFrame                 # 年度财报, index=财年升序, 列见 FIN_COLS
    prices: pd.Series                 # 日收盘价, DatetimeIndex, 至少约 1 年
    eps_fwd: float = float("nan")     # 未来 12 个月一致预期 EPS
    eps_fwd_30d: float = float("nan")  # 30/60/90 天前同一口径的预期 EPS
    eps_fwd_60d: float = float("nan")
    eps_fwd_90d: float = float("nan")
    rev_up_30d: int = 0               # 近 30 天上调盈利预期的分析师数
    rev_down_30d: int = 0
    sector: str = ""

    @property
    def net_debt(self) -> float:
        f = self.fin.sort_index().iloc[-1]
        return float(f["total_debt"] - f["cash"])


def _to_json(s: StockData) -> dict:
    d = {k: getattr(s, k) for k in ["ticker", "price", "shares", "beta", "eps_fwd", "eps_fwd_30d",
                                    "eps_fwd_60d", "eps_fwd_90d", "rev_up_30d", "rev_down_30d", "sector"]}
    d["fin"] = s.fin.reset_index().rename(columns={s.fin.index.name or "index": "year"}).to_dict("list")
    d["prices"] = {str(k.date()): float(v) for k, v in s.prices.items()}
    return d


def _from_json(d: dict) -> StockData:
    fin = pd.DataFrame(d["fin"]).set_index("year")
    prices = pd.Series(d["prices"])
    prices.index = pd.to_datetime(prices.index)
    scalars = {k: d[k] for k in d if k not in ("fin", "prices")}
    return StockData(fin=fin, prices=prices.sort_index(), **scalars)


def save_json(basket: Dict[str, StockData], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump({k: _to_json(v) for k, v in basket.items()}, f, ensure_ascii=False)


def load_json(path: str) -> Dict[str, StockData]:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return {k: _from_json(v) for k, v in raw.items()}


def load_prices_csv(path: str, col: str = "close") -> pd.Series:
    """读 date,close 形式的 CSV (用于基准指数)。"""
    df = pd.read_csv(path, parse_dates=[0], index_col=0)
    return df[col].sort_index()


def fetch_yfinance(ticker: str) -> StockData:
    """从 yfinance 取数。

    注意: 本函数写于无法联网的环境, 没有用真实数据测过。
    yfinance 的行名/列名会随版本变, 第一次使用请对照 print(tk.cashflow.index) 检查。
    """
    import yfinance as yf

    tk = yf.Ticker(ticker)
    info = tk.info or {}

    def row(df, *names):
        for n in names:
            if n in df.index:
                return df.loc[n]
        return pd.Series(dtype=float)

    cf, inc, bs = tk.cashflow, tk.financials, tk.balance_sheet
    fin = pd.DataFrame({
        "revenue": row(inc, "Total Revenue"),
        "net_income": row(inc, "Net Income"),
        "ebit": row(inc, "EBIT", "Operating Income"),
        "cfo": row(cf, "Operating Cash Flow", "Total Cash From Operating Activities"),
        "capex": row(cf, "Capital Expenditure", "Capital Expenditures"),
        "total_debt": row(bs, "Total Debt"),
        "cash": row(bs, "Cash And Cash Equivalents", "Cash"),
        "equity": row(bs, "Stockholders Equity", "Total Stockholder Equity"),
    })
    fin.index = pd.to_datetime(fin.index).year
    fin.index.name = "year"
    fin = fin.sort_index().fillna(0.0)

    hist = tk.history(period="2y")["Close"]
    hist.index = hist.index.tz_localize(None) if hist.index.tz is not None else hist.index

    # eps_trend: 行 0q/+1q/0y/+1y, 列 current/7daysAgo/30daysAgo/60daysAgo/90daysAgo
    # 用 0y 与 +1y 按剩余月份加权近似"未来 12 个月", 这里简化为 +1y 与 0y 的平均
    def fwd(col):
        et = tk.eps_trend
        return float(0.5 * (et.loc["0y", col] + et.loc["+1y", col]))

    et_ok = True
    try:
        eps = {c: fwd(c) for c in ["current", "30daysAgo", "60daysAgo", "90daysAgo"]}
    except Exception:
        et_ok, eps = False, {}
    try:
        er = tk.eps_revisions
        up, down = int(er.loc["0y", "upLast30days"]), int(er.loc["0y", "downLast30days"])
    except Exception:
        up = down = 0
    nan = float("nan")
    return StockData(
        ticker=ticker, price=float(info.get("currentPrice") or hist.iloc[-1]),
        shares=float(info.get("sharesOutstanding") or nan), beta=float(info.get("beta") or 1.0),
        fin=fin, prices=hist,
        eps_fwd=eps.get("current", nan) if et_ok else nan,
        eps_fwd_30d=eps.get("30daysAgo", nan) if et_ok else nan,
        eps_fwd_60d=eps.get("60daysAgo", nan) if et_ok else nan,
        eps_fwd_90d=eps.get("90daysAgo", nan) if et_ok else nan,
        rev_up_30d=up, rev_down_30d=down, sector=str(info.get("sector", "")),
    )


def demo_basket(n: int = 12, seed: int = 0) -> Dict[str, StockData]:
    """随机假数据, 只用于演示和测试。"""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=300)
    out = {}
    for i in range(n):
        tkr = f"DEMO{i:02d}"
        rev0 = rng.uniform(5e9, 5e10)
        g = rng.uniform(-0.03, 0.15)
        margin = rng.uniform(0.05, 0.25)
        years = [2021, 2022, 2023, 2024]
        rev = np.array([rev0 * (1 + g) ** k for k in range(4)])
        ni = rev * margin * rng.uniform(0.85, 1.15, 4)
        fin = pd.DataFrame({
            "revenue": rev, "net_income": ni, "ebit": ni * 1.3,
            "cfo": ni * rng.uniform(0.8, 1.4, 4), "capex": -rev * rng.uniform(0.02, 0.08, 4),
            "total_debt": rev * rng.uniform(0.05, 0.4), "cash": rev * rng.uniform(0.03, 0.2),
            "equity": rev * rng.uniform(0.3, 0.9),
        }, index=pd.Index(years, name="year"))
        shares = rng.uniform(5e8, 3e9)
        eps = ni[-1] / shares
        drift = rng.uniform(-0.0003, 0.0008)
        px = 50 * np.exp(np.cumsum(rng.normal(drift, 0.015, len(days))))
        prices = pd.Series(px, index=days)
        revision = rng.uniform(-0.1, 0.12)
        eps_fwd = eps * (1 + g) * (1 + revision)
        out[tkr] = StockData(
            ticker=tkr, price=float(px[-1]), shares=shares, beta=float(rng.uniform(0.6, 1.5)),
            fin=fin, prices=prices, eps_fwd=eps_fwd,
            eps_fwd_30d=eps_fwd / (1 + revision * 0.3), eps_fwd_60d=eps_fwd / (1 + revision * 0.6),
            eps_fwd_90d=eps_fwd / (1 + revision),
            rev_up_30d=int(rng.integers(0, 10)), rev_down_30d=int(rng.integers(0, 10)),
        )
    return out
