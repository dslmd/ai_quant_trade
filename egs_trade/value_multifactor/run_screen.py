"""选股器入口。

用法:
  python egs_trade/value_multifactor/run_screen.py --demo                       # 假数据跑通流程
  python egs_trade/value_multifactor/run_screen.py --json basket.json --bench spy.csv --save
  python egs_trade/value_multifactor/run_screen.py --tickers AAPL MSFT --bench-ticker SPY   # 需联网 yfinance
  python egs_trade/value_multifactor/run_screen.py --evaluate --json basket_now.json --bench spy_now.csv
"""
import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from quant_brain.value_factor.data import demo_basket, fetch_yfinance, load_json, load_prices_csv
from quant_brain.value_factor.factors import Config, screen
from quant_brain.value_factor.tracker import evaluate_snapshot, load_snapshots, save_snapshot

pd.set_option("display.width", 220)
COLS = ["price", "fwd_pe", "fwd_pe_trend", "dcf_mos", "rev_90d", "rev_breadth", "roic", "mom_12_1", "score", "pick"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--bench", help="基准指数 CSV (date,close)")
    ap.add_argument("--bench-ticker")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--save", action="store_true", help="保存快照, 供之后验证胜率")
    ap.add_argument("--evaluate", action="store_true", help="用当前价格验证历史快照")
    a = ap.parse_args()

    if a.demo:
        basket = demo_basket(20, 1)
    elif a.json:
        basket = load_json(a.json)
    elif a.tickers:
        basket = {t: fetch_yfinance(t) for t in a.tickers}
    else:
        ap.error("需要 --demo / --json / --tickers 之一")

    bench = None
    if a.bench:
        bench = load_prices_csv(a.bench).iloc[-1]
    elif a.bench_ticker:
        import yfinance as yf
        bench = float(yf.Ticker(a.bench_ticker).history(period="5d")["Close"].iloc[-1])

    if a.evaluate:
        now = {k: v.price for k, v in basket.items()}
        for snap in load_snapshots():
            print(evaluate_snapshot(snap, now, bench))
        return

    t = screen(basket, Config(), top_n=a.top)
    print(t[COLS].round(3).to_string())
    print("\n未通过原因:")
    for tk, r in t[~t["pass_gates"]].iterrows():
        print(f"  {tk}: {r['fail_reasons']}")
    if a.save:
        if bench is None:
            ap.error("--save 需要基准价 (--bench 或 --bench-ticker)")
        print("快照已保存:", save_snapshot(t, bench))


if __name__ == "__main__":
    main()
