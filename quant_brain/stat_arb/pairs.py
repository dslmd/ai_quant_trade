"""协整配对交易: 选对 -> 对冲比例 -> 价差 z-score 信号 -> 带成本回测。"""
import itertools

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import coint


def hedge_ratio(y: pd.Series, x: pd.Series):
    """OLS: y = a + b*x, 返回 (a, b)。价差 = y - b*x - a。"""
    b, a = np.polyfit(x.values, y.values, 1)
    return a, b


def half_life(spread: pd.Series) -> float:
    """OU 回归半衰期: d(s) = k*s_{t-1} + e, 半衰期 = -ln2/k。k>=0 说明不回归, 返回 inf。"""
    s = spread.values
    ds, lag = np.diff(s), s[:-1]
    k = np.polyfit(lag, ds, 1)[0]
    return float(-np.log(2) / k) if k < 0 else float("inf")


def find_pairs(prices: pd.DataFrame, p_max: float = 0.05, hl_range=(2, 60)) -> pd.DataFrame:
    """对 prices 的所有两两组合做 Engle-Granger 协整检验。

    注意多重检验: 检验 N 对会有约 p_max*N 对纯属偶然通过, 要用样本外再验证。
    """
    rows = []
    for a, b in itertools.combinations(prices.columns, 2):
        p = coint(np.log(prices[a]), np.log(prices[b]))[1]
        if p > p_max:
            continue
        la, lb = np.log(prices[a]), np.log(prices[b])
        c, beta = hedge_ratio(la, lb)
        hl = half_life(la - beta * lb - c)
        if hl_range[0] <= hl <= hl_range[1]:
            rows.append({"y": a, "x": b, "pvalue": p, "beta": beta, "half_life": hl})
    return pd.DataFrame(rows).sort_values("pvalue") if rows else pd.DataFrame(columns=["y", "x", "pvalue", "beta", "half_life"])


def backtest_pair(y: pd.Series, x: pd.Series, formation: int = 250, entry: float = 2.0,
                  exit_: float = 0.5, stop: float = 4.0, cost_bps: float = 10.0) -> pd.DataFrame:
    """前 formation 天估参数, 之后不再更新 (无前视); z 超过 entry 开仓, 回到 exit_ 平仓, 超过 stop 止损。

    仓位: +1 做多价差 (多 y 空 beta*x), -1 做空价差。收益用对数价差的日变化, cost_bps 按每次换仓的单边成本。
    """
    ly, lx = np.log(y), np.log(x)
    c, beta = hedge_ratio(ly[:formation], lx[:formation])
    spread = ly - beta * lx - c
    mu, sd = spread[:formation].mean(), spread[:formation].std()
    z = (spread - mu) / sd

    pos, cur = [], 0
    for v in z.values:
        if cur == 0:
            cur = -1 if v > entry else (1 if v < -entry else 0)
        elif abs(v) < exit_ or abs(v) > stop:
            cur = 0
        pos.append(cur)
    pos = pd.Series(pos, index=z.index).shift(1).fillna(0)   # 用昨天的信号, 避免前视
    pnl = pos * spread.diff().fillna(0)
    cost = pos.diff().abs().fillna(0) * cost_bps / 1e4
    out = pd.DataFrame({"z": z, "pos": pos, "pnl": pnl - cost})
    return out.iloc[formation:]
