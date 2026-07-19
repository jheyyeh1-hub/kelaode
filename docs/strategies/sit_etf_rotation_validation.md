# SIT ETF rotation real-data validation

Generated 2026-07-19T04:54:51.551278+00:00. Data sources actually used: AKShare fund_etf_hist_em / Eastmoney, AKShare fund_etf_hist_sina / Sina. Adjustment status is recorded per symbol in `data_manifest.json`. Raw files and results are ignored by Git.

## Data coverage
- 510300: 2012-05-28 – 2026-07-17 (3436 sessions)
- 510500: 2013-03-15 – 2026-07-17 (3239 sessions)
- 159915: 2011-12-09 – 2026-07-17 (3544 sessions)
- 512100: 2016-11-04 – 2026-07-17 (2355 sessions)
- 512880: 2016-08-08 – 2026-07-17 (2413 sessions)
- 512480: 2019-06-12 – 2026-07-17 (1722 sessions)
- 518880: 2013-07-29 – 2026-07-17 (3153 sessions)
- 513100: 2013-05-15 – 2026-07-17 (3202 sessions)
- 511010: 2013-04-09 – 2026-07-17 (3226 sessions)

No pre-listing history was backfilled. This present-day ETF universe has survivorship/selection bias. Provider adjustment methodologies were not independently verified; unadjusted Sina fallbacks omit distribution adjustment and qfq series are not claimed to be fully audited total-return series.

## Design
- Boundaries: `{'warmup_start': datetime.date(2019, 6, 12), 'train_start': datetime.date(2020, 1, 2), 'train_end': datetime.date(2022, 12, 30), 'validation_start': datetime.date(2023, 1, 3), 'validation_end': datetime.date(2024, 6, 28), 'test_start': datetime.date(2024, 7, 1), 'data_end': datetime.date(2026, 7, 17)}`. Warm-up is excluded from metrics.
- Grid: `{"max_weight":[0.5,1.0],"momentum_lookback":[63,126,189],"rebalance_frequency":["monthly"],"top_k":[1,2,3],"trend_window":[null,200],"volatility_lookback":[null,60]}` (60 legal combinations after filtering combinations where `top_k * max_weight < 1`). Single process; each result is flushed immediately and experiment IDs resume without rerunning.
- Selection: validation Calmar, max drawdown <=35%, >=5 trades; then Sharpe, lower drawdown, turnover, parameter JSON. Test was run once after freezing.
- Signal at close, next-session open fill; commission/minimum commission/slippage, 100-share lots, cash and exposure constraints are applied.

## Results
- Fixed default: total return 27.5667%, Sharpe 0.302, max drawdown -44.7647%, turnover 5.214.
- Selected parameters: `{"max_weight":1.0,"minimum_listing_age":200,"momentum_lookback":126,"rebalance_frequency":"monthly","top_k":2,"trend_window":200,"volatility_lookback":60}`. Validation Calmar 3.885, return 38.5630%.
- Frozen test: total return 31.5557%, CAGR 14.9517%, Sharpe 0.571, max drawdown -42.6946%, turnover 4.486.
- Walk-forward: 7 folds. Parameters stable in 1/7 folds. See CSV for stitched daily OOS equity and fold metrics.

## Benchmarks and costs
- 510300_buy_hold: return 31.2420%, Sharpe 0.782, drawdown -16.2516%
- nine_etf_equal_weight_buy_hold: return 46.4598%, Sharpe 0.957, drawdown -23.9944%
- cross_sectional_momentum: return -6.9200%, Sharpe 0.060, drawdown -39.3646%
- sit_fixed_default: return 26.3366%, Sharpe 0.810, drawdown -21.3291%
- sit_selected: return 31.5557%, Sharpe 0.571, drawdown -42.6946%
- sit_walk_forward_oos: return -2.9280%, Sharpe 0.049, drawdown -25.7458%

- base: return 31.5557%, final 131555.75, total cost 452.05
- 2x_commission: return -14.4316%, final 85568.39, total cost 1979.81
- 2x_slippage: return -21.8492%, final 78150.85, total cost 1818.65
- 2x_both: return 30.9815%, final 130981.55, total cost 903.05
- 4x_both: return 48.5256%, final 148525.61, total cost 299.39

## Interpretation and limitations
Economic significance is determined by the aligned benchmark table rather than in-sample performance. Parameter selection can overfit the short validation windows; walk-forward dispersion is the primary robustness check. Results remain exposed to universe survivorship bias, fund closure omission, qfq methodology limitations, and simplified liquidity/limit metadata. No missing result is inferred or fabricated.
