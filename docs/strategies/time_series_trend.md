# Multi-Asset Time-Series Momentum with Volatility Scaling

`TimeSeriesTrendStrategy` is the repository's second independent strategy family. Its economic hypothesis is that an ETF's own positive price trend may persist, while inverse-volatility sizing can reduce the dominance of noisier active assets. This implementation is for synthetic infrastructure validation, not an investment recommendation or a performance claim.

## Exact rule

For symbol *i* at completed close *t*, using only that symbol's observations,

`trend_i(t) = close_i(t) / close_i(t - trend_lookback) - 1`.

The symbol is trend-positive only when the signal is **strictly greater** than `signal_buffer`. Realized volatility is the sample standard deviation (Bessel-corrected denominator `n - 1`) of exactly `volatility_lookback` completed close-to-close returns, annualized by `sqrt(252)`. Volatility that is nonfinite, zero, or no greater than the documented numerical floor `1e-12` is ineligible. Eligible raw scores are `1 / realized_volatility`; they are normalized to unit gross exposure, so capital remains in cash when execution constraints, lot rounding, costs, or the absence of eligible assets prevent investment. There is no shorting or leverage.

## Schedule, capacity, and missing observations

The first union-calendar session on which any asset has enough own observations for both calculations is a rebalance date. Complete targets are then updated every configured number of observed union-calendar sessions; intervening calls return `HoldTargets`. An unavailable current bar, insufficient own trend history, insufficient own return history, or invalid volatility excludes only that symbol. History is never common-date truncated, so later listings and missing dates do not remove valid history from other assets. A temporarily unavailable next open is handled explicitly by the shared execution engine and rejected notional is not redistributed.

When `maximum_active_assets` is enabled and binds, capacity is assigned in a preregistered order: strongest absolute positive own-trend signal, then lower realized volatility, then canonical symbol order. This is only a capacity control after independent classification, not cross-sectional top-k momentum.

Signals formed at close *t* enter the shared pending-target path and can execute only at a later open. The shared engine applies cash buffer, maximum single-symbol weight, maximum gross exposure, lot size, participation, commissions, slippage, suspension, and no-shorting constraints.

## Diagnostics and auditability

The sealed daily audit records the point-in-time trend, realized volatility, eligibility/inactive reason, raw inverse-volatility score, normalized target, rebalance flag, active count, and Herfindahl target concentration. These values contain no future observations and permit reconstruction of each rebalance target.

## Strengths and expected failure modes

Potential strengths are transparent own-history signals, deterministic behavior, natural cash allocation in negative regimes, and lower sizing for noisier eligible assets. Expected failure modes include:

* trend whipsaw;
* prolonged range-bound markets;
* volatility-estimate instability;
* correlated simultaneous signals;
* concentration in low-volatility assets;
* delayed reaction after a regime reversal; and
* excessive turnover at short rebalance intervals.

## Distinction from SIT and research scope

Unlike `SITMomentumRotationStrategy`, this rule does not rank the universe for primary selection, choose top-k assets, or call SIT candidate-selection code. Every symbol first passes or fails its own absolute trend rule. No frozen SIT strategy, protocol, snapshot, judgment, or result artifact is used or changed. The committed configurations use a separate synthetic snapshot and a deliberately two-candidate infrastructure grid. This PR performs no formal real-market parameter selection and makes no economic-performance, profitability, or deployability conclusion.
