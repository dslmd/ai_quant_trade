"""选股快照与前向验证。

没有"当时可得"的历史分析师预期数据, 前瞻PE和盈利修正没法做无偏的历史回测。
所以这里用前向跟踪: 每次选股存快照, 3/6 个月后拿真实价格对比基准, 累计胜率。
"""
import json
import os
from datetime import date
from typing import Optional

import pandas as pd

SNAP_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "runtime", "value_screener", "snapshots")


def save_snapshot(table: pd.DataFrame, benchmark_price: float, asof: Optional[str] = None,
                  directory: str = SNAP_DIR) -> str:
    asof = asof or date.today().isoformat()
    os.makedirs(directory, exist_ok=True)
    picks = table[table["pick"]]
    snap = {
        "asof": asof,
        "benchmark_price": benchmark_price,
        "picks": {tk: float(r["price"]) for tk, r in picks.iterrows()},
        # 没入选的也记录价格, 以后可检验"筛掉的是不是真的更差"
        "rejected": {tk: float(r["price"]) for tk, r in table[~table["pick"]].iterrows()},
        "scores": {tk: float(r["score"]) for tk, r in table.iterrows()},
    }
    path = os.path.join(directory, f"{asof}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    return path


def evaluate_snapshot(snap: dict, prices_now: dict, benchmark_now: float) -> dict:
    """prices_now: {ticker: 现价}。返回等权组合超额收益和单股胜率。"""
    bench_ret = benchmark_now / snap["benchmark_price"] - 1

    def rets(group):
        return pd.Series({tk: prices_now[tk] / p0 - 1 for tk, p0 in group.items() if tk in prices_now})

    pr, rr = rets(snap["picks"]), rets(snap["rejected"])
    return {
        "asof": snap["asof"],
        "benchmark_return": bench_ret,
        "picks_return": pr.mean(),
        "excess": pr.mean() - bench_ret,
        "hit_rate": float((pr > bench_ret).mean()) if len(pr) else float("nan"),   # 单股跑赢大盘比例
        "rejected_hit_rate": float((rr > bench_ret).mean()) if len(rr) else float("nan"),  # 对照: 被筛掉的
        "n": len(pr),
    }


def load_snapshots(directory: str = SNAP_DIR) -> list:
    if not os.path.isdir(directory):
        return []
    out = []
    for fn in sorted(os.listdir(directory)):
        if fn.endswith(".json"):
            with open(os.path.join(directory, fn), encoding="utf-8") as f:
                out.append(json.load(f))
    return out
