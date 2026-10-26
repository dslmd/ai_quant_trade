"""三因子 + 质量/动量选股: 前瞻市盈率(价值)、DCF 安全边际(质量/价值)、盈利预期修正(基本面动量)。

流程: 单股因子 -> 硬性门槛(挡价值陷阱) -> 篮子内分位数打分 -> 排序选 Top N。
篮子很小时 (几十只) z-score 不稳, 所以用分位数排名。
"""
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from quant_brain.value_factor.data import StockData
from quant_brain.value_factor.dcf import DCFInputs, dcf_value, fade_growth, margin_of_safety
from quant_brain.value_factor.fcf import cagr, free_cash_flow_firm


NOT_APPLICABLE_SECTORS = {"Financial Services", "Real Estate"}


@dataclass
class Config:
    risk_free: float = 0.04
    equity_premium: float = 0.05
    terminal_growth: float = 0.025
    growth_cap: float = 0.15          # 显式期起始增长率上限
    growth_haircut: float = 0.7       # 对历史增长打折, DCF 宁可保守
    rate_floor: float = 0.08          # 折现率夹在 [8%, 12%], 避免 beta 噪音
    rate_cap: float = 0.12
    # 硬性门槛
    min_mos: Optional[float] = 0.0    # DCF 安全边际下限; None 表示不设这道门槛
    max_pe_trend: float = 0.10        # 前瞻 PE 90 天涨幅上限 (越高越像价值陷阱)
    min_revision: float = 0.0         # 90 天 EPS 预期修正下限
    min_fcf_positive: int = 3         # 近 4 年 FCF 为正的年数
    max_net_debt_ebit: float = 4.0    # 净债务/EBIT 上限
    min_roic: Optional[float] = None         # ROIC 下限 (税后 EBIT / 投入资本)
    require_above_200d: bool = True
    max_fwd_pe: Optional[float] = None       # 前瞻 PE 上限
    min_eps_growth: Optional[float] = None   # 当年与次年预期 EPS 增速下限 (盈利要在增长)
    min_mom: Optional[float] = None          # 12-1 动量下限
    max_mom: Optional[float] = None          # 12-1 动量上限 (排除已经抛物线的)
    # 分组权重 (持有 3-6 个月: 修正和动量权重高于纯估值)
    weights: Dict[str, float] = None

    def __post_init__(self):
        if self.weights is None:
            self.weights = {"value": 0.25, "dcf": 0.25, "revision": 0.30, "quality": 0.10, "momentum": 0.10,
                            "growth": 0.0}


def smallcap_momentum_config() -> "Config":
    """小盘动量 + 盈利加速 + 前瞻PE不离谱。DCF 不设门槛 (小盘成长股的 FCF 常被再投资压低)。"""
    return Config(
        min_mos=None, min_fcf_positive=2, max_net_debt_ebit=3.0, max_pe_trend=0.15,
        max_fwd_pe=35.0, min_eps_growth=0.10, min_mom=0.15, max_mom=2.5, min_roic=0.08,
        weights={"momentum": 0.30, "growth": 0.25, "revision": 0.25, "value": 0.10, "quality": 0.10, "dcf": 0.0},
    )


def _price_at(prices: pd.Series, days_back: int) -> float:
    ts = prices.index[-1] - pd.Timedelta(days=days_back)
    s = prices[:ts]
    return float(s.iloc[-1]) if len(s) else float("nan")


def stock_factors(s: StockData, cfg: Config) -> dict:
    fin = s.fin.sort_index()
    fcf = free_cash_flow_firm(fin["cfo"], fin["capex"])
    f: dict = {"ticker": s.ticker, "price": s.price, "sector": s.sector}

    # --- 价值: 前瞻 PE 及 90 天趋势 ---
    f["fwd_pe"] = s.price / s.eps_fwd if s.eps_fwd > 0 else np.nan
    px90 = _price_at(s.prices, 90)
    pe90 = px90 / s.eps_fwd_90d if s.eps_fwd_90d > 0 else np.nan
    f["fwd_pe_trend"] = f["fwd_pe"] / pe90 - 1 if pe90 == pe90 and f["fwd_pe"] == f["fwd_pe"] else np.nan

    # --- DCF: 3 年平均 FCF, 历史增长打折后线性衰减到永续增长 ---
    base = float(fcf.tail(3).mean())
    g_hist = cagr(fcf)
    if g_hist != g_hist:
        g_hist = cagr(fin["revenue"])
    g0 = float(np.clip((g_hist if g_hist == g_hist else 0.0) * cfg.growth_haircut, 0.0, cfg.growth_cap))
    rate = float(np.clip(cfg.risk_free + s.beta * cfg.equity_premium, cfg.rate_floor, cfg.rate_cap))
    if base > 0 and s.shares > 0:
        v = dcf_value(DCFInputs(base, fade_growth(g0, cfg.terminal_growth, 10), rate,
                                cfg.terminal_growth, s.net_debt, s.shares))
        f["dcf_value"], f["dcf_mos"], f["tv_share"] = v["value_per_share"], margin_of_safety(v["value_per_share"], s.price), v["terminal_share"]
    else:
        f["dcf_value"] = f["dcf_mos"] = f["tv_share"] = np.nan
    f["fcf_yield"] = base / (s.price * s.shares) if s.shares > 0 else np.nan

    # --- 盈利预期修正: 幅度 + 广度 ---
    f["rev_90d"] = s.eps_fwd / s.eps_fwd_90d - 1 if s.eps_fwd_90d > 0 and s.eps_fwd == s.eps_fwd else np.nan
    n = s.rev_up_30d + s.rev_down_30d
    f["rev_breadth"] = (s.rev_up_30d - s.rev_down_30d) / n if n else np.nan

    # --- 质量: ROIC, 杠杆, FCF 稳定性, FCF 转化率 ---
    last = fin.iloc[-1]
    invested = last["equity"] + last["total_debt"] - last["cash"]
    f["roic"] = last["ebit"] * 0.75 / invested if invested > 0 else np.nan
    f["net_debt_ebit"] = s.net_debt / last["ebit"] if last["ebit"] > 0 else np.nan
    f["fcf_pos_years"] = int((fcf.tail(4) > 0).sum())
    f["fcf_conv"] = fcf.iloc[-1] / last["net_income"] if last["net_income"] > 0 else np.nan

    # --- 价格动量 12-1 (剔除最近 1 个月反转), 和 6 个月 ---
    p1, p12 = _price_at(s.prices, 21), _price_at(s.prices, 365)
    f["mom_12_1"] = p1 / p12 - 1 if p12 == p12 and p12 > 0 else np.nan
    f["above_200d"] = bool(s.price > s.prices.tail(200).mean())
    p6 = _price_at(s.prices, 183)
    f["mom_6_1"] = p1 / p6 - 1 if p6 == p6 and p6 > 0 else np.nan

    # --- 盈利加速: 预期增速 (当年/次年), 次年相对当年的加速, 超预期, 营收同比; PEG ---
    f["eps_g0"], f["eps_g1"] = s.eps_growth_0y, s.eps_growth_1y
    f["eps_accel"] = s.eps_growth_1y - s.eps_growth_0y
    f["surprise"], f["rev_yoy_q"] = s.surprise_avg4, s.rev_yoy_q
    f["peg"] = f["fwd_pe"] / (100 * s.eps_growth_1y) if s.eps_growth_1y > 0 and f["fwd_pe"] == f["fwd_pe"] else np.nan
    return f


def build_factor_table(basket: Dict[str, StockData], cfg: Config = None) -> pd.DataFrame:
    cfg = cfg or Config()
    return pd.DataFrame([stock_factors(s, cfg) for s in basket.values()]).set_index("ticker")


def apply_gates(t: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """硬性门槛。每个门槛不通过都记录原因, 方便复盘。"""
    reasons = pd.Series([[] for _ in range(len(t))], index=t.index, dtype=object)

    def fail(mask, msg):
        for tk in t.index[mask]:
            reasons[tk] = reasons[tk] + [msg]

    fail(t["sector"].isin(NOT_APPLICABLE_SECTORS), "金融/地产股,FCF-DCF不适用")
    fail(~(t["fwd_pe"] > 0), "无正的前瞻EPS或无分析师覆盖")
    if cfg.min_mos is not None:
        fail(~(t["dcf_mos"] >= cfg.min_mos), "DCF无安全边际/无正FCF")
    if cfg.max_fwd_pe is not None:
        fail(t["fwd_pe"] > cfg.max_fwd_pe, f"前瞻PE>{cfg.max_fwd_pe:g}")
    if cfg.min_eps_growth is not None:
        fail(~((t["eps_g0"] >= cfg.min_eps_growth) & (t["eps_g1"] >= cfg.min_eps_growth)), "预期EPS增速不足")
    if cfg.min_mom is not None:
        fail(~(t["mom_12_1"] >= cfg.min_mom), "动量不足")
    if cfg.max_mom is not None:
        fail(t["mom_12_1"] > cfg.max_mom, "涨幅过大(抛物线)")
    fail(~(t["fwd_pe_trend"] <= cfg.max_pe_trend), "前瞻PE在抬升(陷阱)")
    fail(~(t["rev_90d"] >= cfg.min_revision), "盈利预期被下调")
    fail(t["fcf_pos_years"] < cfg.min_fcf_positive, "FCF不稳定")
    fail(t["net_debt_ebit"].isna(), "EBIT<=0,杠杆无法衡量")   # NaN 比较恒为 False, 不单独拦会被放行
    fail(t["net_debt_ebit"] > cfg.max_net_debt_ebit, "杠杆过高")
    if cfg.min_roic is not None:
        fail(~(t["roic"] >= cfg.min_roic), f"ROIC<{cfg.min_roic:.0%}")
    if cfg.require_above_200d:
        fail(~t["above_200d"], "跌破200日线(落刀)")
    out = t.copy()
    out["fail_reasons"] = reasons.map(lambda r: "; ".join(r))
    out["pass_gates"] = reasons.map(len) == 0
    return out


def composite_score(t: pd.DataFrame, cfg: Config) -> pd.Series:
    """篮子内分位数 (0~1, 越高越好), 按组加权。缺失值取 0.5 中性, 但门槛已拦掉关键缺失。"""
    def r(col, higher_better=True):
        x = t[col].rank(pct=True, ascending=higher_better)
        return x.fillna(0.5)

    groups = {
        "value": (r("fwd_pe", False) + r("fwd_pe_trend", False)) / 2,
        "dcf": (r("dcf_mos") + r("fcf_yield")) / 2,
        "revision": (r("rev_90d") + r("rev_breadth")) / 2,
        "quality": (r("roic") + r("net_debt_ebit", False) + r("fcf_conv")) / 3,
        "momentum": (r("mom_12_1") + r("mom_6_1")) / 2,
        "growth": (r("eps_g1") + r("eps_accel") + r("surprise") + r("rev_yoy_q") + r("peg", False)) / 5,
    }
    score = sum(cfg.weights.get(k, 0.0) * v for k, v in groups.items())
    for k, v in groups.items():
        t[f"g_{k}"] = v
    return score / sum(cfg.weights.values())


def screen(basket: Dict[str, StockData], cfg: Config = None, top_n: int = 5) -> pd.DataFrame:
    """返回全部股票的因子、门槛结果、总分; 通过门槛的按分数排在前面。"""
    cfg = cfg or Config()
    t = apply_gates(build_factor_table(basket, cfg), cfg)
    t["score"] = composite_score(t, cfg)
    t = t.sort_values(["pass_gates", "score"], ascending=[False, False])
    t["pick"] = False
    t.loc[t.index[t["pass_gates"]][:top_n], "pick"] = True
    return t
