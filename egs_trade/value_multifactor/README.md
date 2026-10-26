# 价值多因子选股器

目标: 从一篮子股票里挑出未来 3 到 6 个月更可能跑赢大盘的几只。

## 逻辑

三个因子加一道落刀过滤,每一步都对应一个要避开的坑。

| 因子 | 指标 | 作用 |
|---|---|---|
| 价值 | 前瞻 PE 低,且 90 天内没有抬升 | 找便宜,PE 抬升说明在变贵或预期被下调 |
| 质量/DCF | 3 年均 FCF 做 DCF,安全边际 > 0;FCF 为正的年数、ROIC、净债务/EBIT | 剔除便宜但现金流差的价值陷阱 |
| 基本面动量 | 90 天 EPS 预期修正幅度、30 天上调/下调广度 | 持有 3 到 6 个月最直接的催化剂 |
| 价格动量 | 12-1 个月收益,收盘价在 200 日线上 | 不接落刀 |

流程: 因子 → 硬门槛 → 篮子内分位数打分(价值 25%、DCF 25%、修正 30%、质量 10%、动量 10%) → 取前 N 只。
门槛不通过的股票会写明原因。权重和阈值在 `quant_brain/value_factor/factors.py` 的 `Config`。

DCF 刻意保守: 历史 FCF 增速打 7 折、封顶 15%、10 年线性衰减到 2.5%,折现率限制在 8% 到 12%。
`dcf.py` 里有敏感性表和反向 DCF(看股价隐含多高增长)。

## 用法

```bash
python egs_trade/value_multifactor/run_screen.py --demo                      # 假数据跑通流程
python egs_trade/value_multifactor/run_screen.py --tickers AAPL MSFT --bench-ticker SPY --save   # 联网
python egs_trade/value_multifactor/run_screen.py --json basket.json --bench spy.csv --save       # 离线
```

`--save` 把本次入选名单和价格存到 `runtime/value_screener/snapshots/`。
几个月后用 `--evaluate` 对照真实价格,得到等权超额收益和单股跑赢大盘的比例,以及被筛掉那批股票的同一指标做对照。

## 怎么衡量"跑赢大盘胜率"

- 没有"当时可得"的历史分析师预期数据,前瞻 PE 和盈利修正无法做无偏的历史回测。所以用前向跟踪累积样本。
- 一次只有 3 到 5 只,胜率噪音很大。要看 20 次以上快照的累计结果才有意义。
- 基准要和篮子市场一致(美股用 SPY/IWV,A 股用沪深 300 或中证全指)。
- 篮子本身有偏(比如你只给我科技股),跑赢"大盘"可能只是押对了行业。快照里的 `rejected` 就是用来对照这个。

## 当前限制

- 这个环境连不上 Yahoo 等数据站(代理拒绝),`fetch_yfinance` 没有用真实数据测过,字段名可能要调整。已验证的部分是:因子计算、DCF、门槛、打分、快照读写,都是用假数据和单元测试验证的。
- A 股需要换数据源(tushare、baostock 等),把数据整理成 `StockData` 即可,其他代码不用改。
- 假数据只用来演示流程,输出的"入选股"没有任何意义。

## 相关

`quant_brain/stat_arb/`: 协整配对交易(找对、半衰期、z-score 信号、带成本回测)和风险指标(VaR、CVaR、回撤、夏普)。
与选股器独立,持有期和逻辑不同,暂时不在选股流程里。
