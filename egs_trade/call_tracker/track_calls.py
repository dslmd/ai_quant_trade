"""统计博主喊单的盈亏。

用小时线回放: 先判断挂单有没有成交, 成交后看先碰到目标还是止损, 再算持有 3 个月/6 个月/至今
的收益并和 SPY 同期对比。用法: python egs_trade/call_tracker/track_calls.py [calls.json]

约定(都偏保守或已说明):
- 限价单: 消息发出后 10 个交易日内有效; 宽松成交=碰到区间上沿, 严格成交=碰到区间下沿。
- 市价模拟: 消息发出后第一根小时线的开盘价。
- 同一根小时线里既碰止损又碰目标, 按先止损算。
- 没给止损/目标的, 只看持有收益和期间最大回撤。
"""
import json
import os
import sys

import numpy as np
import pandas as pd
import yfinance as yf

H1M, H3M, H6M = 20, 63, 126   # 交易日
VALID_DAYS = 10
LENIENCY = pd.Timedelta(minutes=10)
TZ = "America/New_York"


def load_prices(tickers, start, end):
    syms = sorted(set(tickers) | {"SPY"})
    hr = yf.download(syms, start=start, end=end, interval="1h", auto_adjust=False, progress=False, group_by="ticker")
    dy = yf.download(syms, start=start, end=end, interval="1d", auto_adjust=False, progress=False, group_by="ticker")
    for d in (hr, dy):
        if d.index.tz is None:
            d.index = d.index.tz_localize(TZ)
        else:
            d.index = d.index.tz_convert(TZ)
    return hr, dy


def evaluate(call, hr, dy, strict=False):
    t = call["ticker"]
    bars = hr[t].dropna(subset=["Close"])
    posted = pd.Timestamp(call["posted"]).tz_localize(TZ)
    bars = bars[bars.index >= posted - LENIENCY]
    if bars.empty:
        return {"id": call["id"], "status": "无数据"}
    expiry = None
    if call["type"] == "setup":
        lo, hi = call["entry"]
        days = sorted(set(bars.index.normalize()))
        expiry = days[min(VALID_DAYS, len(days) - 1)] + pd.Timedelta(days=1)
        cand = bars[bars.index < expiry]
        level = lo if strict else hi
        hit = cand[cand["Low"] <= level]
        if hit.empty:
            later = bars[bars["Low"] <= level]
            return {"id": call["id"], "ticker": t, "status": "未成交", "later_fill": (later.index[0].date().isoformat() if len(later) else None)}
        fb = hit.iloc[0]
        fill = min(fb["Open"], level)
        fill_ts = hit.index[0]
    else:
        fb = bars.iloc[0]
        fill, fill_ts = fb["Open"], bars.index[0]

    after = bars[bars.index > fill_ts]
    target = call.get("target") or (fill + call["target_pts"] if call.get("target_pts") else None)
    stop = call.get("stop")
    outcome, exit_px, exit_ts = "持有中", None, None
    for ts, b in after.iterrows():
        if stop is not None and b["Low"] <= stop:
            outcome, exit_px, exit_ts = "止损", min(b["Open"], stop), ts
            break
        if target is not None and b["High"] >= target:
            outcome, exit_px, exit_ts = "达标", max(b["Open"], target), ts
            break
    end_ts = exit_ts or after.index[-1] if len(after) else fill_ts

    d = dy[t].dropna(subset=["Close"])
    fd = fill_ts.normalize()
    di = d.index.searchsorted(fd)
    out = {"id": call["id"], "ticker": t, "status": "成交", "fill_time": fill_ts.strftime("%m-%d %H:%M"), "fill": round(float(fill), 2),
           "outcome": outcome}
    if exit_px is not None:
        out["rule_exit"] = round(float(exit_px), 2)
        out["rule_ret"] = exit_px / fill - 1
        out["days_to_exit"] = int(np.busday_count(fd.date(), exit_ts.date()))
    # 期间最大回撤/最大浮盈 (到规则出场或 3 个月)
    horizon_end = d.index[min(di + H3M, len(d) - 1)]
    stop_at = exit_ts if exit_ts is not None else horizon_end + pd.Timedelta(days=1)
    seg = after[after.index <= stop_at]
    if len(seg):
        out["mae"] = seg["Low"].min() / fill - 1
        out["mfe"] = seg["High"].max() / fill - 1
    spy_in = hr["SPY"].dropna(subset=["Close"])
    spy_in = spy_in[spy_in.index >= fill_ts].iloc[0]["Open"]
    for name, n in (("1m", H1M), ("3m", H3M), ("6m", H6M)):
        j = di + n
        if j < len(d):
            out[f"ret_{name}"] = d["Close"].iloc[j] / fill - 1
            out[f"ex_{name}"] = out[f"ret_{name}"] - (dy["SPY"]["Close"].iloc[j] / spy_in - 1)
    # 短线口径: 20 个交易日内触发目标/止损按规则价出场, 否则按第 20 个交易日收盘价
    if "rule_ret" in out and out["days_to_exit"] <= H1M:
        out["short_ret"] = out["rule_ret"]
    elif "ret_1m" in out:
        out["short_ret"] = out["ret_1m"]
    out["ret_now"] = d["Close"].iloc[-1] / fill - 1
    out["ex_now"] = out["ret_now"] - (dy["SPY"]["Close"].iloc[-1] / spy_in - 1)
    return out


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "calls.json")
    calls = json.load(open(path, encoding="utf-8"))["calls"]
    first = min(pd.Timestamp(c["posted"]) for c in calls) - pd.Timedelta(days=5)
    hr, dy = load_prices({c["ticker"] for c in calls}, first.strftime("%Y-%m-%d"), (pd.Timestamp.today() + pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
    print("数据截至", dy.index[-1].date())
    rows = {}
    for mode in ("loose", "strict"):
        rows[mode] = [evaluate(c, hr, dy, strict=(mode == "strict")) for c in calls if c["type"] != "avoid"]
    avoid = []
    for c in calls:
        if c["type"] == "avoid":
            avoid.append(evaluate({**c, "type": "market"}, hr, dy))
    out = {k: pd.DataFrame(v) for k, v in rows.items()}
    out["avoid"] = pd.DataFrame(avoid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out["loose"].to_csv(os.path.join(os.path.dirname(path), "result_loose.csv"), index=False)
    out["strict"].to_csv(os.path.join(os.path.dirname(path), "result_strict.csv"), index=False)
    return out, calls


if __name__ == "__main__":
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    o, calls = main()
    kind = {c["id"]: c["type"] for c in calls}
    for name in ("loose", "strict"):
        df = o[name].copy()
        df["type"] = df["id"].map(kind)
        print(f"\n=== {name} ===")
        pct = [c for c in df.columns if c.startswith(("ret_", "ex_", "rule_ret", "short_ret", "mae", "mfe"))]
        print(df.assign(**{c: (df[c] * 100).round(1) for c in pct}).to_string(index=False))
    print("\n=== avoid ==="); print(o["avoid"].assign(**{c: (o["avoid"][c] * 100).round(1) for c in o["avoid"].columns if c.startswith(("ret_", "ex_"))}).to_string(index=False))
