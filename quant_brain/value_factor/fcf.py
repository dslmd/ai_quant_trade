"""自由现金流 (FCF) 计算。

输入是按年排列的 DataFrame, index 为财年 (升序), 列名见各函数注释。
金额单位只要前后一致即可。
"""
import numpy as np
import pandas as pd


def free_cash_flow_firm(cfo: pd.Series, capex: pd.Series) -> pd.Series:
    """FCF = 经营现金流 - 资本开支。

    capex 无论传正数还是负数 (现金流量表里通常是负数) 都按支出处理。
    注意: 这是简化口径, 利息已在 CFO 内扣除, 严格的 FCFF 应加回税后利息,
    对低负债公司差别不大。
    """
    return cfo - capex.abs()


def free_cash_flow_from_ebit(ebit: pd.Series, tax_rate: float, da: pd.Series,
                             capex: pd.Series, delta_nwc: pd.Series) -> pd.Series:
    """FCFF = EBIT*(1-t) + 折旧摊销 - 资本开支 - 营运资本增加。"""
    return ebit * (1 - tax_rate) + da - capex.abs() - delta_nwc


def free_cash_flow_equity(fcff: pd.Series, interest: pd.Series, tax_rate: float,
                          net_borrowing: pd.Series) -> pd.Series:
    """FCFE = FCFF - 税后利息 + 净借款。"""
    return fcff - interest * (1 - tax_rate) + net_borrowing


def cagr(series: pd.Series) -> float:
    """首尾复合增长率。首尾非正数时增长率无意义, 返回 nan。"""
    s = series.dropna()
    if len(s) < 2 or s.iloc[0] <= 0 or s.iloc[-1] <= 0:
        return float("nan")
    return float((s.iloc[-1] / s.iloc[0]) ** (1 / (len(s) - 1)) - 1)


def fcf_summary(fin: pd.DataFrame, shares: float, price: float = None) -> dict:
    """汇总 FCF 关键指标。

    fin 需含列 cfo, capex, revenue; 可选 net_income。
    返回: 最近一年 FCF、3 年平均 (标准化)、FCF 利润率、FCF 增长率、
    FCF 转化率 (FCF/净利润)、每股 FCF、FCF 收益率。
    """
    fin = fin.sort_index()
    fcf = free_cash_flow_firm(fin["cfo"], fin["capex"])
    last3 = fcf.tail(3)
    out = {
        "fcf_last": float(fcf.iloc[-1]),
        "fcf_avg3": float(last3.mean()),
        "fcf_margin": float(fcf.iloc[-1] / fin["revenue"].iloc[-1]),
        "fcf_cagr": cagr(fcf),
        "fcf_positive_years": int((fcf > 0).sum()),
        "fcf_per_share": float(fcf.iloc[-1] / shares),
    }
    if "net_income" in fin and fin["net_income"].iloc[-1] > 0:
        out["fcf_conversion"] = float(fcf.iloc[-1] / fin["net_income"].iloc[-1])
    if price:
        out["fcf_yield"] = out["fcf_per_share"] / price
    return out
