"""常用风险与绩效指标 (输入为日收益率 Series)。"""
import numpy as np
import pandas as pd
from scipy.stats import norm


def sharpe(r: pd.Series, periods: int = 252) -> float:
    return float(r.mean() / r.std() * np.sqrt(periods)) if r.std() > 0 else float("nan")


def max_drawdown(r: pd.Series) -> float:
    eq = (1 + r).cumprod()
    return float((eq / eq.cummax() - 1).min())


def var_historical(r: pd.Series, level: float = 0.95) -> float:
    """历史模拟 VaR, 返回正的损失比例。"""
    return float(-np.percentile(r.dropna(), (1 - level) * 100))


def var_parametric(r: pd.Series, level: float = 0.95) -> float:
    return float(-(r.mean() + norm.ppf(1 - level) * r.std()))


def cvar_historical(r: pd.Series, level: float = 0.95) -> float:
    """尾部期望损失: 比 VaR 更能反映极端情形。"""
    cut = -var_historical(r, level)
    return float(-r[r <= cut].mean())
