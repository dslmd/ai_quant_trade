"""自动联网取数 (美股, 数据源 Yahoo Finance, 经 yfinance)。

做的事: 年报三表 -> 自由现金流相关字段; analyst 的 eps_trend / eps_revisions -> 前瞻 EPS 与修正;
日线 -> 价格与动量。每只股票单独容错, 带磁盘缓存和重试, 并输出数据质量警告。

需要放行的域名 (沙箱默认拒绝): query1.finance.yahoo.com, query2.finance.yahoo.com,
fc.yahoo.com, finance.yahoo.com, guce.yahoo.com / consent.yahoo.com (cookie 与 crumb 握手可能用到)。
"""
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from quant_brain.value_factor.data import StockData, load_json, save_json

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "runtime", "value_screener", "cache")

# yfinance 行名在不同版本/公司之间会变, 按优先级依次尝试
ROW_ALIASES = {
    "revenue": ["Total Revenue", "Operating Revenue"],
    "net_income": ["Net Income", "Net Income Common Stockholders"],
    "ebit": ["EBIT", "Operating Income"],
    "cfo": ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"],
    "capex": ["Capital Expenditure", "Purchase Of PPE"],
    "total_debt": ["Total Debt"],
    "cash": ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"],
    "equity": ["Stockholders Equity", "Common Stock Equity"],
}
REQUIRED = ["revenue", "cfo", "ebit", "net_income"]      # 缺了就不能算, 直接放弃该股票
DEFAULT_ZERO = ["capex", "total_debt", "cash", "equity"]  # 缺了按 0 处理, 但要警告


@dataclass
class FetchReport:
    ticker: str
    ok: bool = True
    from_cache: bool = False
    warnings: List[str] = field(default_factory=list)


def _row(df: pd.DataFrame, names) -> pd.Series:
    if df is None or len(df) == 0:
        return pd.Series(dtype=float)
    for n in names:
        if n in df.index:
            return df.loc[n]
    return pd.Series(dtype=float)


def _fwd_weight(info: dict, today: pd.Timestamp) -> float:
    """'0y' 财年剩余比例。未来 12 个月 EPS ≈ w*EPS(0y) + (1-w)*EPS(+1y)。"""
    nxt = info.get("nextFiscalYearEnd")
    if not nxt:
        return 0.5
    days = (pd.Timestamp(nxt, unit="s") - today).days
    return float(np.clip(days / 365.0, 0.0, 1.0))


def _build_stock(ticker: str, tk, rep: FetchReport) -> StockData:
    info = tk.info or {}
    today = pd.Timestamp.today().normalize()

    # --- 年度财报 ---
    frames = {"inc": tk.income_stmt, "cf": tk.cashflow, "bs": tk.balance_sheet}
    src = {"revenue": "inc", "net_income": "inc", "ebit": "inc", "cfo": "cf", "capex": "cf",
           "total_debt": "bs", "cash": "bs", "equity": "bs"}
    cols = {k: _row(frames[src[k]], ROW_ALIASES[k]) for k in ROW_ALIASES}
    for k in REQUIRED:
        if cols[k].dropna().empty:
            raise ValueError(f"缺少财报字段 {k} (金融股/新上市公司常见)")
    fin = pd.DataFrame(cols)
    fin.index = pd.to_datetime(fin.index).year
    fin.index.name = "year"
    fin = fin[~fin.index.duplicated()].sort_index()
    fin = fin.dropna(subset=["cfo", "revenue"])
    for k in DEFAULT_ZERO:
        if fin[k].isna().all():
            rep.warnings.append(f"财报缺少 {k}, 按 0 处理")
        fin[k] = fin[k].fillna(0.0)
    fin["ebit"] = fin["ebit"].fillna(0.0)
    fin["net_income"] = fin["net_income"].fillna(0.0)
    if len(fin) < 3:
        raise ValueError(f"财报只有 {len(fin)} 年, 至少需要 3 年")
    if len(fin) < 4:
        rep.warnings.append("财报不足 4 年, 'FCF为正年数' 门槛偏严")

    # --- 价格 ---
    hist = tk.history(period="2y", auto_adjust=False)["Close"].dropna()
    if hist.index.tz is not None:
        hist.index = hist.index.tz_localize(None)
    if len(hist) < 250:
        raise ValueError(f"价格历史只有 {len(hist)} 天, 不足以算 12-1 动量")
    price = info.get("currentPrice") or info.get("regularMarketPrice") or float(hist.iloc[-1])
    if hist.index[-1] < today - pd.Timedelta(days=7):
        rep.warnings.append(f"最新价格日期 {hist.index[-1].date()} 偏旧")

    shares = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
    if not shares:
        raise ValueError("取不到股本")

    # --- 分析师预期: eps_trend / eps_revisions ---
    nan = float("nan")
    eps = {c: nan for c in ("current", "30daysAgo", "60daysAgo", "90daysAgo")}
    try:
        et = tk.eps_trend
        w = _fwd_weight(info, today)
        for c in eps:
            eps[c] = w * float(et.loc["0y", c]) + (1 - w) * float(et.loc["+1y", c])
    except Exception as e:  # 小盘股常无覆盖
        rep.warnings.append(f"无盈利预期数据 ({type(e).__name__}); 前瞻PE/修正因子缺失")
    up = down = 0
    try:
        er = tk.eps_revisions
        up, down = int(er.loc["0y", "upLast30days"]), int(er.loc["0y", "downLast30days"])
    except Exception:
        rep.warnings.append("无预期修正广度数据")

    g0 = g1 = surprise = rev_yoy = nan
    try:
        ee = tk.earnings_estimate
        g0, g1 = float(ee.loc["0y", "growth"]), float(ee.loc["+1y", "growth"])
    except Exception:
        rep.warnings.append("无盈利增速预期")
    try:
        eh = tk.earnings_history
        surprise = float(eh["surprisePercent"].dropna().tail(4).mean())
    except Exception:
        pass
    try:
        qi = _row(tk.quarterly_income_stmt, ROW_ALIASES["revenue"]).dropna().sort_index()
        if len(qi) >= 5:
            rev_yoy = float(qi.iloc[-1] / qi.iloc[-5] - 1)
    except Exception:
        pass

    return StockData(
        ticker=ticker, price=float(price), shares=float(shares), beta=float(info.get("beta") or 1.0),
        fin=fin, prices=hist, eps_fwd=eps["current"], eps_fwd_30d=eps["30daysAgo"],
        eps_fwd_60d=eps["60daysAgo"], eps_fwd_90d=eps["90daysAgo"],
        rev_up_30d=up, rev_down_30d=down, sector=str(info.get("sector", "")),
        eps_growth_0y=g0, eps_growth_1y=g1, surprise_avg4=surprise, rev_yoy_q=rev_yoy,
    )


def _default_factory(symbol: str):
    import yfinance as yf
    return yf.Ticker(symbol)


def fetch_stock(ticker: str, cache_dir: str = CACHE_DIR, max_age_hours: float = 12.0,
                retries: int = 3, backoff: float = 2.0,
                factory: Callable = _default_factory) -> Tuple[Optional[StockData], FetchReport]:
    ticker = ticker.upper().strip()
    rep = FetchReport(ticker)
    path = os.path.join(cache_dir, f"{ticker}.json")
    if max_age_hours and os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age_hours * 3600:
        rep.from_cache = True
        return load_json(path)[ticker], rep

    last_err = None
    for i in range(retries):
        try:
            s = _build_stock(ticker, factory(ticker), rep)
            os.makedirs(cache_dir, exist_ok=True)
            save_json({ticker: s}, path)
            return s, rep
        except ValueError as e:        # 数据本身不够, 重试没用
            last_err = e
            break
        except Exception as e:          # 网络/限流: 退避重试
            last_err = e
            time.sleep(backoff * (2 ** i))
    rep.ok = False
    rep.warnings.append(f"取数失败: {type(last_err).__name__}: {last_err}")
    return None, rep


def fetch_basket(tickers: List[str], pause: float = 0.5, **kw) -> Tuple[Dict[str, StockData], List[FetchReport]]:
    basket, reports = {}, []
    for t in tickers:
        s, rep = fetch_stock(t, **kw)
        reports.append(rep)
        if s is not None:
            basket[s.ticker] = s
        if not rep.from_cache:
            time.sleep(pause)
    return basket, reports


def fetch_benchmark(symbol: str = "SPY", factory: Callable = _default_factory) -> pd.Series:
    """基准日线收盘价 (未复权, 与个股口径一致)。"""
    h = factory(symbol).history(period="2y", auto_adjust=False)["Close"].dropna()
    if h.index.tz is not None:
        h.index = h.index.tz_localize(None)
    if h.empty:
        raise ValueError(f"取不到基准 {symbol}")
    return h
