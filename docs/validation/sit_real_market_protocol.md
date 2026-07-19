# Frozen protocol: first formal real-market SIT validation

**Protocol status:** frozen before final result inspection.  This document was committed before
the final fixed-selection or walk-forward metrics were inspected.  Its universe, grid, periods,
objective, costs, and decision rules must not be changed in response to results.  Any objective
data unavailability requires stopping, documenting and committing a replacement protocol,
regenerating every identity, and restarting the entire experiment.  PR #9 and PR #10 are obsolete
and are neither inputs nor implementations for this work.

## Research question and immutable data

The question is whether the fixed-rule `SITMomentumRotationStrategy`, selected only on validation
observations, passes the criteria below on a never-used frozen test and on stitched walk-forward
OOS observations.  This is research validation, not optimization or an investment recommendation.

The snapshot request is **2005-01-01 through 2026-07-17**, frozen once as normalized CSV by
**AKShare/Eastmoney**, endpoint **`fund_etf_hist_em`**, with **forward-adjusted (`qfq`)** daily OHLCV
for every symbol.  No fallback provider, endpoint, or adjustment is permitted.  The actual range
of each file and the common range will be copied verbatim from the freeze diagnostics; experiments
use the common range beginning no earlier than 2019-06-12 and ending no later than 2026-07-17.
The schema-2.0 manifest records UTC download time, requested/actual ranges, row counts and SHA-256.
CSV and result bundles remain Git-ignored; only the small manifest metadata may be committed.

Freeze validation requires unique strictly increasing dates; finite positive OHLC with
`high >= max(open, close)` and `low <= min(open, close)`; finite nonnegative volume; exact hashes;
one adjustment; and diagnostics for >25% adjacent-close jumps, dates missing from the union
calendar, and observations before official listing.  A provider failure fails the complete freeze
without publishing a valid immutable snapshot.

## Frozen nine-ETF universe

No silent substitution or removal is allowed.  Exchange codes and asset classes are protocol
metadata; listing dates are checked against provider observations and reported again in the final
provenance table.

| Symbol | Exchange | Asset class | Official listing date |
|---|---|---|---|
| 510300 | SSE | CSI 300 broad equity ETF | 2012-05-28 |
| 510500 | SSE | CSI 500 broad equity ETF | 2013-03-15 |
| 159915 | SZSE | ChiNext broad equity ETF | 2011-12-09 |
| 512100 | SSE | CSI 1000 broad equity ETF | 2017-08-25 |
| 512880 | SSE | securities-industry equity ETF | 2016-08-08 |
| 512480 | SSE | semiconductor-industry equity ETF | 2019-06-12 |
| 518880 | SSE | physical-gold commodity ETF | 2013-07-29 |
| 513100 | SSE | NASDAQ-100 cross-border equity ETF | 2013-05-15 |
| 511010 | SSE | government-bond ETF | 2013-03-25 |

For each, the final report must state provider, endpoint, qfq adjustment, requested and actual
start/end, exchange, class, listing date, rows, relative filename, and file SHA-256.

## Portfolio, alignment, and execution

* Calendar: union of snapshot dates, with point-in-time availability and no forward fill.
* Signal/execution: signal after close *t*, execute at next available open *t+1*; test begins with
  cash and no positions. Warm-up supplies history but cannot queue or execute orders.
* Initial cash: CNY 100,000. Lot size: 100 shares. Long-only; gross exposure <= 100%; individual
  weight <= 50%; 1% cash buffer; no leverage; 10% of reported daily volume participation cap;
  partial fills are allowed and the remainder is cancelled.
* Base costs: commission 0.025% of notional, CNY 5 minimum per fill, no stamp duty, and 0.05%
  one-way slippage.
* Primary benchmark and pass/fail comparator: equal-weight buy-and-hold of all nine ETFs. It uses
  identical cash, evaluation dates, next-open execution, lot/participation constraints and costs.
  A 510300 single-ETF comparison may appear only as a secondary post-selection diagnostic.

## Frozen periods

Dates are absolute and are not chosen after observing performance.  If a boundary is not a trading
day, the runner resolves it to available dates inside that inclusive interval and persists every
resolved date.

* Snapshot/experiment range: 2019-06-12 through 2026-07-17 (subject only to an earlier provider
  actual end, which requires stopping and revising this protocol before any final run).
* History-only warm-up: the 252 union-calendar observations immediately preceding each validation
  start. This exceeds the largest 126-observation momentum/trend lookback.
* Fixed train boundary (recorded but unused because SIT is no-fit): 2019-06-12--2020-06-30.
* Fixed validation: 2020-07-01--2022-12-30.
* Frozen test: 2023-01-03--2026-07-17. It is invoked exactly once for the selected candidate.
* Walk-forward: expanding; 252 train, 252 validation, 126 OOS test, step 126, warm-up 252; maximum
  12 folds. Fold OOS dates must be unique and nonoverlapping. Selection sees train/validation only.

## Frozen candidate grid

The Cartesian grid has **16 candidates**, hard-capped at `maximum_candidate_count = 16`; execution
is deterministic and serial. All candidates use `minimum_listing_age=252`, monthly rebalance,
`max_weight=0.5`, and the strategy-native constructor.

| Parameter | Allowed values | Rationale / expected effect |
|---|---|---|
| momentum lookback | 63, 126 | economically interpretable quarter/half-year horizons; longer is smoother |
| top-k | 2, 3 | concentrated versus modestly diversified rotation |
| trend window | disabled, 126 | tests no filter versus a medium-horizon risk-off filter |
| volatility lookback | disabled, 63 | equal allocation versus inverse-volatility allocation |
| rebalance frequency | monthly only | controls turnover without timing search |
| minimum listing age | 252 only | requires roughly one trading year of history |
| maximum weight | 0.50 only | prevents a single holding exceeding half the portfolio |

These values and expected effects were chosen before final metrics inspection. No new value,
universe member, test period, or result-driven grid expansion is permitted.

## Selection and deterministic ranking

The sole objective is **maximize validation Sharpe**, subject to validation maximum drawdown being
no worse than **-35.0%** and at least 252 validation observations. Ineligible/failed candidates are
retained with reasons. Ties resolve by (1) lower turnover, (2) lower complexity (no trend filter,
then no volatility weighting), and (3) canonical parameter JSON. Frozen-test, walk-forward OOS,
and benchmark outcomes are forbidden in selection and tie-breaking.

## Predefined cost analyses

Only the selected strategy is rerun closed-loop. Fixed-path replay holds base fill symbols, sides,
quantities, order and raw-price path invariant and persists its SHA-256, starting CNY 100,000 cash,
and empty starting positions.

| Scenario | Commission | Minimum | One-way slippage |
|---|---:|---:|---:|
| base | 0.025% | CNY 5 | 0.05% |
| moderate | 0.050% | CNY 5 | 0.10% |
| severe plausible | 0.100% | CNY 10 | 0.20% |

Base replay must reconcile to original final equity within CNY 0.000001. For the same frozen path,
nonnegative incremental costs cannot improve final equity.

## Frozen judgment rules

All infrastructure, hash, identity, leakage, accounting, benchmark-alignment, and replay audits are
mandatory; any failure is **FAIL**.

* **PASS:** audits pass; frozen-test net excess total return over the primary benchmark is strictly
  >0.00 percentage points; stitched walk-forward OOS total return is >0.00%; stitched maximum
  drawdown is >=-35.00%; and moderate closed-loop final equity is >=95% of base final equity.
* **CONDITIONAL:** audits pass and no FAIL trigger occurs, but at least one PASS condition is missed;
  or one ETF/one calendar year supplies >60% of positive P&L; or severe-cost final equity is <90%
  of base; or adjacent one-factor candidates reverse the sign of test excess return.
* **FAIL:** frozen-test total return <=-20.00%; stitched OOS total return <=-20.00%; stitched maximum
  drawdown <-50.00%; moderate closed-loop final equity <90% of base; any adjustment/accounting/hash/
  identity/look-ahead/OOS-overlap audit fails; only an unselected candidate was tested; or any
  post-result protocol change is needed.

PASS takes precedence only when all PASS clauses and no CONDITIONAL concentration/sensitivity clause
hold. FAIL takes precedence over both. Otherwise the outcome is CONDITIONAL. These numeric rules
are frozen now and will be evaluated mechanically rather than invented after results.

## Audit, diagnostics, and reporting boundary

A read-only auditor will consume only immutable snapshot metadata and final artifacts. It verifies
hashes and identities; equity/cash/marks/positions; fills/trades/turnover; benchmark dates; no
warm-up orders or pre-listing positions; future-data mutation isolation; disjoint OOS dates and
stitched continuity; replay reconciliation; candidate membership; and selected-only frozen tests.
It cannot select or run a strategy.

Only after primary artifacts are sealed may labeled diagnostics examine one-factor neighbors,
calendar periods, ETF contribution, trade/turnover concentration, market regimes, costs, and fold
parameter stability. Diagnostics cannot redefine the official result. The report must include the
specified return, risk, relative, trading and cost metrics, limitations, identities, artifact
locations and exact reproduction commands. No broker, live/paper trading, scheduling, or allocation
recommendation is in scope.
