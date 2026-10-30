"""画"按博主喊单买入"的收益曲线。

每条喊单成交后投入固定金额(默认 1 万美元), 画累计盈亏占已投入资金的比例:
  - 一直持有到今天
  - 按喊单里的目标/止损出场(没给目标止损的一直持有)
  - 对照: 同样的钱在同样的日期买 SPY
用法: python egs_trade/call_tracker/plot_equity.py [calls.json] [--notional 10000]
"""
import argparse
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager

sys.path.insert(0, os.path.dirname(__file__))
import track_calls as T

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
BLUE, ORANGE, GRAY = "#2a78d6", "#eb6834", "#8a8985"   # 蓝/橙已过 validate_palette.js; 灰是对照(背景)


def setup_font():
    for f in font_manager.findSystemFonts():
        if "wqy" in f.lower() or "notosanscjk" in f.lower().replace("-", ""):
            font_manager.fontManager.addfont(f)
            plt.rcParams["font.family"] = font_manager.FontProperties(fname=f).get_name()
            break
    plt.rcParams["axes.unicode_minus"] = False


def curves(res: pd.DataFrame, dy, notional):
    """返回逐日累计盈亏(美元)与已投入本金, 三条线: 持有 / 规则出场 / SPY。"""
    res = res[res["status"] == "成交"]
    days = dy["SPY"].dropna(subset=["Close"]).index
    days = days[days >= res["fill_ts"].min().normalize()]
    pnl = {k: pd.Series(0.0, index=days) for k in ("hold", "rule", "spy")}
    cost = pd.Series(0.0, index=days)
    for _, r in res.iterrows():
        t = r["ticker"]
        close = dy[t]["Close"].reindex(days).ffill()
        fd = r["fill_ts"].normalize()
        live = days >= fd
        sh = notional / r["fill"]
        cost[live] += notional
        pnl["hold"][live] += (sh * close - notional)[live]
        v = sh * close
        if pd.notna(r["exit_ts"]):
            after = days >= r["exit_ts"].normalize()
            v[after] = sh * r["exit_px"]
        pnl["rule"][live] += (v - notional)[live]
        spy_open = T_spy_open(r["fill_ts"])
        spy_sh = notional / spy_open
        pnl["spy"][live] += (spy_sh * dy["SPY"]["Close"].reindex(days).ffill() - notional)[live]
    return pnl, cost


_HR = None


def T_spy_open(ts):
    s = _HR["SPY"].dropna(subset=["Close"])
    return float(s[s.index >= ts].iloc[0]["Open"])


def draw(ax, pnl, cost, title, n):
    pct = {k: v / cost * 100 for k, v in pnl.items()}
    x = pct["hold"].index
    ax.axhline(0, color=INK2, lw=0.8, zorder=1)
    ax.plot(x, pct["spy"], color=GRAY, lw=2, solid_capstyle="round", zorder=2)
    ax.plot(x, pct["hold"], color=BLUE, lw=2, solid_capstyle="round", zorder=3)
    ax.plot(x, pct["rule"], color=ORANGE, lw=2, solid_capstyle="round", zorder=3)
    ends = [("hold", BLUE, "一直持有"), ("rule", ORANGE, "按目标/止损出场"), ("spy", GRAY, "同样买 SPY")]
    vals = sorted([(pct[k].iloc[-1], k, c, l) for k, c, l in ends], key=lambda t: t[0])
    last_y = None
    for v, k, c, l in vals:                      # 末端标注: 圆点带 2px 底色描边; 文字用墨色不用系列色
        ax.plot(x[-1], v, "o", ms=8, mfc=c, mec=SURFACE, mew=2, zorder=4, clip_on=False)
        y = v if last_y is None or v - last_y > 4 else last_y + 4
        d = pnl[k].iloc[-1]
        ax.annotate(f"{l}  {v:+.1f}%  ({'-' if d < 0 else '+'}${abs(d):,.0f})", (x[-1], v), xytext=(10, y - v), textcoords="offset points",
                    fontsize=9.5, color=INK, va="center", annotation_clip=False,
                    arrowprops=dict(arrowstyle="-", color=GRID, lw=0.8) if abs(y - v) > 1 else None)
        last_y = y
    ax.set_title(title, loc="left", fontsize=12, color=INK, pad=24, fontweight="bold")
    ax.text(0, 1.03, f"{n} 条喊单,每条成交后投入同样金额", transform=ax.transAxes, fontsize=9.5, color=INK2)
    ax.set_facecolor(SURFACE)
    ax.grid(axis="y", color=GRID, lw=1)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=9, length=0)
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:+.0f}%"))


def main():
    global _HR
    ap = argparse.ArgumentParser()
    ap.add_argument("calls", nargs="?", default=os.path.join(os.path.dirname(__file__), "calls.json"))
    ap.add_argument("--notional", type=float, default=10_000)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "equity_curve.png"))
    a = ap.parse_args()
    sys.argv = [sys.argv[0], a.calls]
    out, calls, hr, dy = T.main()
    _HR = hr
    res = out["loose"].merge(pd.DataFrame(calls)[["id", "type"]], on="id")
    buys = res[res["status"] == "成交"]
    priced = buys[buys["type"] == "setup"]
    setup_ids = set(priced["id"]) | {"RCAT-1110"}
    priced = buys[buys["id"].isin(setup_ids)]

    setup_font()
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.6), sharey=True, facecolor=SURFACE)
    for ax, df, title in ((axes[0], buys, "全部看多的喊单"), (axes[1], priced, "只算给了价位的喊单")):
        pnl, cost = curves(df, dy, a.notional)
        draw(ax, pnl, cost, title, len(df))
    handles = [plt.Line2D([], [], color=c, lw=2, label=l) for c, l in ((BLUE, "一直持有"), (ORANGE, "按目标/止损出场"), (GRAY, "同样的钱同样的日期买 SPY"))]
    fig.legend(handles=handles, loc="upper right", ncol=3, frameon=False, fontsize=10, labelcolor=INK2, bbox_to_anchor=(0.80, 0.985))
    fig.suptitle("如果照着博主的喊单买:累计收益占已投入资金的比例", x=0.01, ha="left", fontsize=14, color=INK, fontweight="bold")
    fig.text(0.01, 0.015, "成交按挂单价/发消息后开盘价(宽松成交),价格为 Yahoo 未复权小时线和日线;蓝线与橙线的分母是已投入的本金。不构成投资建议。",
             fontsize=8.5, color=INK2)
    fig.subplots_adjust(left=0.05, right=0.80, top=0.82, bottom=0.12, wspace=0.62)
    fig.savefig(a.out, dpi=160, facecolor=SURFACE)
    print("saved", a.out)
    return buys, priced


if __name__ == "__main__":
    main()
