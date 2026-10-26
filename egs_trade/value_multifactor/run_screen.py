"""选股器入口。

用法:
  python egs_trade/value_multifactor/run_screen.py --demo                       # 假数据跑通流程
  python egs_trade/value_multifactor/run_screen.py --json basket.json --bench spy.csv --save
  python egs_trade/value_multifactor/run_screen.py --tickers AAPL MSFT NVDA --save            # 联网自动取数, 基准 SPY
  python egs_trade/value_multifactor/run_screen.py --tickers-file basket.txt --refresh --save  # 忽略缓存重新取
  python egs_trade/value_multifactor/run_screen.py --evaluate --json basket_now.json --bench spy_now.csv
"""
import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from quant_brain.value_factor.data import demo_basket, load_json, load_prices_csv
from quant_brain.value_factor.fetch import fetch_basket, fetch_benchmark
from quant_brain.value_factor.factors import Config, screen, smallcap_momentum_config
from quant_brain.value_factor.universe import smallcap_momentum_universe
from quant_brain.value_factor.tracker import evaluate_snapshot, load_snapshots, save_snapshot

pd.set_option("display.width", 220)
COLS_SMALL = ["price", "fwd_pe", "peg", "eps_g0", "eps_g1", "eps_accel", "rev_90d", "surprise", "rev_yoy_q", "mom_6_1", "mom_12_1", "score", "pick"]
COLS = ["price", "fwd_pe", "fwd_pe_trend", "dcf_mos", "rev_90d", "rev_breadth", "roic", "mom_12_1", "score", "pick"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--tickers-file", help="每行一个代码, # 开头为注释")
    ap.add_argument("--profile", choices=["value", "smallcap", "smallcap-relaxed"], default="value",
                    help="value: 价值+DCF+修正; smallcap: 小盘动量+盈利加速+前瞻PE不离谱")
    ap.add_argument("--universe", choices=["smallcap"], help="先用 Yahoo 选股器预筛候选池 (需联网)")
    ap.add_argument("--broad", action="store_true", help="放宽预筛条件, 候选更多")
    ap.add_argument("--max-names", type=int, default=60, help="预筛后最多取数多少只")
    ap.add_argument("--refresh", action="store_true", help="忽略缓存")
    ap.add_argument("--bench", help="基准指数 CSV (date,close)")
    ap.add_argument("--bench-ticker", default="SPY")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--save", action="store_true", help="保存快照, 供之后验证胜率")
    ap.add_argument("--evaluate", action="store_true", help="用当前价格验证历史快照")
    a = ap.parse_args()

    if a.demo:
        basket = demo_basket(20, 1)
    elif a.json:
        basket = load_json(a.json)
    elif a.tickers or a.tickers_file or a.universe:
        tickers = list(a.tickers or [])
        if a.universe:
            u = smallcap_momentum_universe(**(dict(mcap=(300e6, 8e9), mom_52w=(15, 300), min_eps_growth=5, min_rev_growth=5) if a.broad else {}))
            print(f"预筛得到 {len(u)} 只候选")
            tickers += u["symbol"].head(a.max_names).tolist()
        if a.tickers_file:
            with open(a.tickers_file, encoding="utf-8") as f:
                tickers += [ln.split("#")[0].strip() for ln in f if ln.split("#")[0].strip()]
        basket, reports = fetch_basket(sorted(set(tickers)), max_age_hours=0 if a.refresh else 12)
        for r in reports:
            tag = "缓存" if r.from_cache else ("OK" if r.ok else "失败")
            print(f"[{tag}] {r.ticker}" + (": " + "; ".join(r.warnings) if r.warnings else ""))
        if not basket:
            sys.exit("一只都没取到。若是网络问题: 沙箱需放行 query1/query2.finance.yahoo.com, fc.yahoo.com")
        print()
    else:
        ap.error("需要 --demo / --json / --tickers / --tickers-file 之一")

    bench = None
    if a.bench:
        bench = load_prices_csv(a.bench).iloc[-1]
    elif not a.demo:
        bench = float(fetch_benchmark(a.bench_ticker).iloc[-1])

    if a.evaluate:
        now = {k: v.price for k, v in basket.items()}
        for snap in load_snapshots():
            print(evaluate_snapshot(snap, now, bench))
        return

    cfg = Config() if a.profile == "value" else smallcap_momentum_config(relaxed=a.profile.endswith("relaxed"))
    t = screen(basket, cfg, top_n=a.top)
    print(t[COLS if a.profile == "value" else COLS_SMALL].round(3).to_string())
    print("\n未通过原因:")
    for tk, r in t[~t["pass_gates"]].iterrows():
        print(f"  {tk}: {r['fail_reasons']}")
    if a.save:
        if bench is None:
            ap.error("--save 需要基准价 (--bench 或 --bench-ticker)")
        print("快照已保存:", save_snapshot(t, bench))


if __name__ == "__main__":
    main()
