"""用 Yahoo 自带的选股器预筛候选池, 避免对几千只股票逐个取数。

预筛只用粗条件把范围缩到几十只, 真正的筛选还是 factors.screen。
"""
from typing import List

import pandas as pd


def smallcap_momentum_universe(mcap=(300e6, 6e9), min_price=8.0, min_volume=400_000,
                               mom_52w=(25, 250), min_eps_growth=10, min_rev_growth=8,
                               max_fwd_pe=40.0, limit=250) -> pd.DataFrame:
    """小盘 + 52 周涨幅适中 + EPS/营收在增长 + 前瞻PE不离谱。返回 Yahoo 报价表。"""
    import yfinance as yf
    from yfinance import EquityQuery as Q

    q = Q("and", [
        Q("eq", ["region", "us"]),
        Q("is-in", ["exchange", "NMS", "NYQ", "NGM", "ASE"]),
        Q("btwn", ["intradaymarketcap", mcap[0], mcap[1]]),
        Q("gt", ["intradayprice", min_price]),
        Q("gt", ["avgdailyvol3m", min_volume]),
        Q("btwn", ["fiftytwowkpercentchange", mom_52w[0], mom_52w[1]]),
        Q("gt", ["epsgrowth.lasttwelvemonths", min_eps_growth]),
        Q("gt", ["quarterlyrevenuegrowth.quarterly", min_rev_growth]),
    ])
    rows, offset = [], 0
    while len(rows) < limit:
        r = yf.screen(q, offset=offset, size=min(100, limit - len(rows)), sortField="fiftytwowkpercentchange", sortAsc=False)
        quotes = r.get("quotes", [])
        rows += quotes
        offset += len(quotes)
        if not quotes or offset >= r.get("total", 0):
            break
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df[(df["forwardPE"] > 0) & (df["forwardPE"] <= max_fwd_pe)]
    if {"epsForward", "epsCurrentYear"} <= set(df.columns):
        df = df[df["epsForward"] > df["epsCurrentYear"]]   # 次年预期高于当年: 盈利还在上行
    return df.reset_index(drop=True)


def tickers(df: pd.DataFrame, n: int = 60) -> List[str]:
    return df["symbol"].head(n).tolist()
