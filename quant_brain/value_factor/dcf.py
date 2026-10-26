"""现金流折现 (DCF) 估值。

DCF 的结论对 折现率 和 永续增长率 极敏感, 终值通常占估值 65%~75%,
所以这里同时提供敏感性表和反向 DCF (看当前股价隐含了多高增长)。
"""
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
import pandas as pd


@dataclass
class DCFInputs:
    base_fcf: float                 # 起点 FCF (建议用 3 年平均, 剔除一次性波动)
    growth: Sequence[float]         # 显式预测期每年增长率, 如 5 年 [0.12, 0.10, 0.08, 0.06, 0.05]
    discount_rate: float            # 折现率 (WACC)
    terminal_growth: float = 0.025  # 永续增长率, 不应高于长期名义 GDP
    net_debt: float = 0.0           # 净负债 = 有息负债 - 现金及等价物
    shares: float = 1.0             # 总股本
    extra: dict = field(default_factory=dict)


def wacc(equity_value: float, debt_value: float, risk_free: float, beta: float,
         equity_premium: float, pre_tax_cost_of_debt: float, tax_rate: float) -> float:
    """WACC = E/V * (rf + beta*ERP) + D/V * Kd * (1-t)。"""
    v = equity_value + debt_value
    ke = risk_free + beta * equity_premium
    return equity_value / v * ke + debt_value / v * pre_tax_cost_of_debt * (1 - tax_rate)


def project_fcf(base_fcf: float, growth: Sequence[float]) -> np.ndarray:
    return base_fcf * np.cumprod(1 + np.asarray(growth, dtype=float))


def dcf_value(inp: DCFInputs) -> dict:
    """返回企业价值、股权价值、每股内在价值、终值占比。"""
    r, g = inp.discount_rate, inp.terminal_growth
    if r <= g:
        raise ValueError("折现率必须大于永续增长率, 否则终值发散")
    flows = project_fcf(inp.base_fcf, inp.growth)
    t = np.arange(1, len(flows) + 1)
    pv_flows = float((flows / (1 + r) ** t).sum())
    terminal = flows[-1] * (1 + g) / (r - g)
    pv_terminal = float(terminal / (1 + r) ** len(flows))
    ev = pv_flows + pv_terminal
    equity = ev - inp.net_debt
    return {
        "enterprise_value": ev,
        "equity_value": equity,
        "value_per_share": equity / inp.shares,
        "terminal_share": pv_terminal / ev if ev else float("nan"),
        "pv_flows": pv_flows,
        "pv_terminal": pv_terminal,
    }


def margin_of_safety(value_per_share: float, price: float) -> float:
    """安全边际 = 1 - 价格/内在价值。>0 表示低估。"""
    return 1 - price / value_per_share if value_per_share > 0 else float("nan")


def sensitivity_table(inp: DCFInputs, rates: Sequence[float], terminal_growths: Sequence[float]) -> pd.DataFrame:
    """每股价值随 折现率 x 永续增长率 变化的表。"""
    rows = {}
    for r in rates:
        rows[r] = {}
        for g in terminal_growths:
            if r <= g:
                rows[r][g] = float("nan")
                continue
            i = DCFInputs(inp.base_fcf, inp.growth, r, g, inp.net_debt, inp.shares)
            rows[r][g] = dcf_value(i)["value_per_share"]
    df = pd.DataFrame(rows).T
    df.index.name, df.columns.name = "discount_rate", "terminal_growth"
    return df


def reverse_dcf(price: float, base_fcf: float, discount_rate: float, years: int = 10,
                terminal_growth: float = 0.025, net_debt: float = 0.0, shares: float = 1.0) -> float:
    """反向 DCF: 求使每股价值 = 当前股价 的 `未来 years 年恒定增长率`。

    用二分法。返回值越高, 说明市场定价里藏着越乐观的增长预期。
    若区间内无解 (价格过低或过高) 返回 nan。
    """
    def val(gr):
        return dcf_value(DCFInputs(base_fcf, [gr] * years, discount_rate, terminal_growth,
                                   net_debt, shares))["value_per_share"]

    lo, hi = -0.5, 1.0
    if not (val(lo) <= price <= val(hi)):
        return float("nan")
    for _ in range(100):
        mid = (lo + hi) / 2
        if val(mid) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def fade_growth(start: float, terminal: float, years: int = 10) -> list:
    """增长率从 start 线性衰减到 terminal, 避免对高增长做不现实的永续假设。"""
    return list(np.linspace(start, terminal, years))
