# 统计套利(配对交易)

代码在 `quant_brain/stat_arb/`,测试在 `unit_test/test_value_factor.py`。

- `pairs.find_pairs`: 对所有两两组合做 Engle-Granger 协整检验,再按 OU 半衰期过滤。
  检验的对数多时会有假阳性(5% 显著性下,100 对里约 5 对纯属偶然),必须用样本外数据再验证。
- `pairs.backtest_pair`: 前 formation 天估对冲比例和价差均值/标准差,之后固定不变;
  |z| > 2 开仓,< 0.5 平仓,> 4 止损;信号滞后一天执行,扣单边成本。
- `risk`: 夏普、最大回撤、历史/参数化 VaR、CVaR。

还没做: GARCH 波动率定仓、ARIMA 方向信号、机器学习信号。
高频交易(做市、延迟套利)依赖 co-location 和纳秒级基础设施,个人做不了,所以不在这个库的范围内。
