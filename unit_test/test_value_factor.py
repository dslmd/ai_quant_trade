import numpy as np
import pandas as pd
import pytest

from quant_brain.stat_arb.pairs import backtest_pair, find_pairs, half_life
from quant_brain.stat_arb.risk import max_drawdown, var_historical
from quant_brain.value_factor.data import demo_basket
from quant_brain.value_factor.dcf import DCFInputs, dcf_value, reverse_dcf, sensitivity_table
from quant_brain.value_factor.factors import screen
from quant_brain.value_factor.fcf import cagr, free_cash_flow_firm
from quant_brain.value_factor.tracker import evaluate_snapshot


def test_fcf_capex_sign_agnostic():
    cfo = pd.Series([100.0, 120.0])
    assert (free_cash_flow_firm(cfo, pd.Series([-30.0, -40.0])) == free_cash_flow_firm(cfo, pd.Series([30.0, 40.0]))).all()
    assert cagr(pd.Series([100.0, 121.0, 144.1])) == pytest.approx(0.2, abs=0.01)
    assert np.isnan(cagr(pd.Series([-1.0, 5.0])))


def test_dcf_matches_hand_calc():
    # 零增长永续年金: 价值 = FCF / (r - g) 在 g=0 时 = FCF / r
    v = dcf_value(DCFInputs(100.0, [0.0] * 10, 0.10, 0.0, net_debt=0, shares=1))
    assert v["enterprise_value"] == pytest.approx(1000.0, rel=1e-9)
    with pytest.raises(ValueError):
        dcf_value(DCFInputs(100.0, [0.05], 0.03, 0.03))


def test_sensitivity_monotone_and_reverse_dcf_roundtrip():
    inp = DCFInputs(100.0, [0.05] * 5, 0.10, 0.025, 50.0, 10.0)
    tbl = sensitivity_table(inp, [0.08, 0.10, 0.12], [0.01, 0.025])
    assert tbl.loc[0.08, 0.025] > tbl.loc[0.10, 0.025] > tbl.loc[0.12, 0.025]
    price = dcf_value(DCFInputs(100.0, [0.07] * 10, 0.10, 0.025, 50.0, 10.0))["value_per_share"]
    assert reverse_dcf(price, 100.0, 0.10, 10, 0.025, 50.0, 10.0) == pytest.approx(0.07, abs=1e-4)


def test_screen_gates_and_picks():
    t = screen(demo_basket(20, 1), top_n=3)
    assert t["pick"].sum() <= 3
    assert t.loc[t["pick"], "pass_gates"].all()
    assert (t.loc[~t["pass_gates"], "fail_reasons"] != "").all()
    assert t["score"].between(0, 1).all()


def test_tracker_hit_rate():
    snap = {"asof": "x", "benchmark_price": 100.0, "picks": {"A": 10.0, "B": 10.0}, "rejected": {"C": 10.0}}
    r = evaluate_snapshot(snap, {"A": 12.0, "B": 10.0, "C": 9.0}, 105.0)
    assert r["hit_rate"] == 0.5 and r["benchmark_return"] == pytest.approx(0.05)


def _cointegrated(n=600, seed=0):
    rng = np.random.default_rng(seed)
    x = np.cumsum(rng.normal(0, 1, n)) + 100
    s = np.zeros(n)
    for i in range(1, n):
        s[i] = 0.9 * s[i - 1] + rng.normal(0, 1)
    idx = pd.bdate_range("2020-01-01", periods=n)
    return pd.Series(np.exp((x + s) / 100 + 4), idx), pd.Series(np.exp(x / 100 + 4), idx)


def test_pairs_detect_and_backtest_no_lookahead():
    y, x = _cointegrated()
    rng = np.random.default_rng(1)
    z = pd.Series(np.exp(np.cumsum(rng.normal(0, 0.01, len(y))) + 4), y.index)
    found = find_pairs(pd.DataFrame({"y": y, "x": x, "z": z}))
    assert ((found["y"] == "y") & (found["x"] == "x")).any()
    assert half_life(np.log(y) - np.log(x)) < 60
    bt = backtest_pair(y, x, formation=250)
    assert len(bt) == len(y) - 250 and set(bt["pos"].unique()) <= {-1, 0, 1}
    assert bt["pos"].iloc[0] == 0  # 首日不能持仓: 信号滞后一天


def test_risk_metrics():
    r = pd.Series([0.01, -0.02, 0.015, -0.03, 0.02] * 40)
    assert max_drawdown(r) < 0
    assert var_historical(r, 0.95) > 0
