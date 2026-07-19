# Strategy SDK

Strategies are deliberately separated from execution. A `TargetWeightStrategy` returns final
symbol weights; a `RankingStrategy` returns arbitrary finite scores for a portfolio constructor;
and a `SignalStrategy` returns `LONG`, `FLAT`, or numeric signals for `SignalToWeightAdapter`.
The backtester remains solely responsible for orders, constraints, fills, fees, and accounting.

## Quick start

```python
strategy = CrossSectionalMomentumRotation(lookback=60, top_k=3)
result = PortfolioBacktester().run(data, strategy)
```

Constructors include top/bottom K, score/rank/inverse-volatility weighting, caps, cash buffers,
turnover limits, and long-only/tradability filters. They sort symbol keys for reproducibility.

## Point-in-time rules

Only use `MarketView.history`, `latest`, `returns`, `rolling_window`, or `cross_section`.
These APIs expose data through `current_date`; never retain the backing dataset, center a rolling
window, back-fill a missing observation, or shift data backward. Warm-up periods should hold cash.
Parameters should be frozen dataclasses and serialized with `parameters_json` for experiment IDs.

Common errors are passing a complete DataFrame, treating missing history as zero, placing orders
inside a strategy, and attempting to bypass `ConstraintEngine` or `FillModel`.
