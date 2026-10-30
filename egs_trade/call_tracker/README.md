# 喊单盈亏统计

把博主的喊单整理进 `calls.json`,用 Yahoo 小时线回放,统计盈亏并和 SPY 对比。

```bash
python egs_trade/call_tracker/track_calls.py             # 默认读同目录的 calls.json
python egs_trade/call_tracker/track_calls.py my_calls.json
```

画收益曲线(每条成交后投入同样金额,对比同样买 SPY):

```bash
python egs_trade/call_tracker/plot_equity.py --notional 10000   # 输出 equity_curve.png
```

结果写到同目录的 `result_loose.csv`(宽松成交)和 `result_strict.csv`(严格成交)。

## calls.json 字段

- `posted`: 美东时间。用 IREN 11/3、PGY 11/10 的开盘跳空核对过,博主的时间就是美东。
- `type`: `setup` 给了价位的挂单;`market` 没给价位,按发消息后第一根小时线开盘价买入;`avoid` 建议回避或减仓。
- `entry`: [下沿, 上沿]。单个价位按上下各 1% 展开。`target` / `target_pts` / `stop` 可选。

## 回放规则

- 限价单发出后 10 个交易日内有效。宽松成交是碰到区间上沿,严格成交是碰到区间下沿。
- 同一根小时线里既碰止损又碰目标,按先止损算;止损或目标遇到跳空按开盘价成交。
- 允许消息发出前 10 分钟内开始的小时线(开盘第一根),不会更早。
- 短线口径:20 个交易日内触发目标或止损按规则价出场,否则按第 20 个交易日收盘价。
- 超额收益 = 个股收益 - SPY 同期收益,SPY 起点取成交那根小时线的开盘价。

## 局限

- 只能统计发过来的消息。博主提到的"之前我们入的位置比较高""其他亏损的 POET FIG"等历史仓位没有价格,统计不到。
- "继续持有"这类没有价格的话,按发消息后开盘价买入模拟,和博主真实成本无关。
- 没有仓位大小(只有 RCAT 5%、TSLA 25% 两处),所以只能算每笔的收益率,算不出账户总盈亏。
