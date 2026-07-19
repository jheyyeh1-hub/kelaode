# SIT ETF rotation real-data audit

## Scope and dependency

This is an audit, not an optimization. It uses PR #9 head `33577a0` because PR #9
was not merged when the work began; the audit branch therefore depends on PR #9.
No grid was expanded, no result-driven parameter was selected, and no strategy
rule was added. Generated market data and `results/` remain uncommitted.

## Reproducibility — **FAIL**

PR #9 ignored both its raw cache and numerical artifacts. Its committed report
claims frozen-test return 31.5557%, CAGR 14.9517%, Sharpe 0.571, and drawdown
-42.6946%. A fresh run with the same symbol coverage and provider labels produced
45.1973%, 20.8608%, 0.7683, and -21.8631%, respectively. A second execution on
that newly downloaded cache matched to `1e-10`, but that proves only internal
determinism, not reproduction of PR #9. `reproducibility.json` records SHA,
runtime, peak RSS, Python, dependencies, both value sets, and differences.

Under the specified decision rule, this discrepancy alone requires **FAIL**.

## Data quality and adjustment convention

The cache contains seven Eastmoney qfq series (`510300`, `510500`, `159915`,
`512100`, `512880`, `512480`, `513100`) and two Sina unadjusted fallbacks
(`518880`, `511010`). Thus the nine-ETF result mixes adjustment conventions.
Dates, duplicates, missing union-calendar dates, non-positive values, OHLC/volume
checks, jumps above 15%, source labels, fallback status, and file hashes are in
`data_audit.csv`.

A uniform nine-symbol qfq or unadjusted cache could not be obtained. Running only
the seven qfq symbols would change the universe and is not directly comparable;
it was therefore not presented as a substitute result. Without a reliable,
uniform data set or corporate-action ledger, these results have no investment-
conclusion value.

## Accounting and metric reconciliation

The independent calculator exactly reconciled total return, CAGR, volatility,
Sharpe, drawdown, turnover, and commission for the newly generated equity/trade
files. No negative cash, illegal weight, unexplained position mutation, or
end-of-day identity violation was observed in the framework's daily audit records.
No accounting or metric-formula error was found in that rerun.

The PR #9 cost table is not a same-order-path comparison: 13 base trades become
58 at 2x-both and only 2 at 4x-both. The 4x result rises to 48.53%. Higher costs
change affordability, lots, and subsequent targets, so the table cannot establish
cost monotonicity and does not satisfy the requested cost audit.

## Time boundaries and look-ahead

`MarketView` bounds history, latest values, returns, and listing age at the current
date. Orders retain a signal date and execute on a later trade date. Rolling train,
validation, and test intervals are ordered and stitched OOS dates are unique.
The point-in-time test confirms changing bars strictly after a signal boundary does
not change targets through that boundary. No future function was found. The PR #9
benchmark code, however, does not persist signal/fill traces for every benchmark,
so exact execution-timing parity cannot be independently reconstructed.

## Frozen-test attribution and maximum drawdown

The claimed 31.56% run cannot be attributed because its equity, trades, daily
positions, and source cache were not committed and the rerun differs materially.
It would be misleading to label the rerun attribution as attribution of that claim.
In the rerun's closed-lot accounting, gold contributed +3,725.73 realized while
159915 contributed -5,384.37, 513100 -1,996.03, and 512880 -463.09; an open
512480 lot prevents a complete realized/unrealized decomposition from the saved
trade file alone. Accordingly, neither “the claimed gain mainly came from one ETF”
nor “it survives removal of the largest winner” is established.

Likewise the claimed -42.69% episode's dates, holdings, and trigger cannot be
reconstructed from PR #9. The fresh rerun's maximum drawdown is -21.86%, not the
claimed episode. This is an artifact-retention/auditability failure, not evidence
that the earlier drawdown disappeared.

## Walk-forward diagnosis

The committed workflow has seven non-overlapping folds:

| Fold | Test | Parameters (lookback/top-k/max weight) | Test return | Sharpe | Drawdown | Trades |
|---:|---|---|---:|---:|---:|---:|
| 0 | 2022-08-09–2023-02-16 | 126/2/1.0 | -4.75% | -2.074 | -5.18% | 13 |
| 1 | 2023-02-17–2023-08-21 | 63/2/1.0 | -5.40% | -0.623 | -9.28% | 12 |
| 2 | 2023-08-22–2024-03-01 | 189/2/0.5 | +9.82% | 2.123 | -4.15% | 3 |
| 3 | 2024-03-04–2024-09-03 | 126/2/0.5 | +6.63% | 0.989 | -9.16% | 9 |
| 4 | 2024-09-04–2025-03-18 | 189/3/1.0 | -13.01% | -1.112 | -16.54% | 23 |
| 5 | 2025-03-19–2025-09-17 | 189/1/1.0 | +34.51% | 1.970 | -12.86% | 1 |
| 6 | 2025-09-18–2026-04-01 | 63/1/1.0 | -8.37% | -0.355 | -19.73% | 1 |

Only 3/7 folds (42.9%) are profitable. Parameters switch at every boundary (six
switches); no selection repeats, and the best fold has only one trade. The median
fold return is -4.75%. The large +34.51% fold masks four losing folds, while the
-13.01% fold is the largest drag. These facts explain why the reported stitched
OOS result is negative: validation choices are unstable and generalize poorly,
not merely because of one small cost charge. Per-fold benchmark curves were not
saved, so the requested per-fold 510300/equal-weight win rates and remove-best-
fold statistic cannot be responsibly manufactured.

## Parameter selection and local sensitivity

The configured grid has 72 raw and 60 legal combinations; `top_k * max_weight < 1`
is excluded. Selection sorts eligible validation rows by Calmar, Sharpe, lower
drawdown, turnover, then canonical parameters, and applies the 35% drawdown limit
only to validation. But `experiment_id` hashes only parameters, dates, and explicit
cost overrides. It omits universe, cached data hashes, default fees, initial cash,
execution configuration, code SHA, and strategy version, so stale cache reuse is
possible. “Test only once” is not enforceable from ignored artifacts.

The diagnostic neighborhood is not a smooth plateau: moving the selected rerun
lookback to 151 reduces return from 45.20% to 8.78%, and volatility lookback 72
reduces it to 12.71%. Some other perturbations are identical because they do not
alter discrete orders. This is material overfitting risk; none of these values was
used to select a replacement parameter.

## Benchmark fairness and costs

PR #9 aligns its benchmark summary to the fixed test dates and initial capital,
but uses separate benchmark strategy implementations and does not persist their
orders, cash, positions, or fold-level curves. Missing-data and fee/timing parity
therefore cannot be completely audited. It also lacks the requested cash benchmark.
The reported fixed-test comparison must not be compared directly with the longer
walk-forward OOS interval. Given these gaps and the non-monotone changing-order
cost paths, benchmark fairness is not demonstrated.

## Final determination — **FAIL**

This is not a PASS and not merely CONDITIONAL. The required FAIL triggers include:

1. PR #9's committed claims are not reproducible from retained artifacts.
2. qfq and unadjusted sources are materially mixed.
3. Reported walk-forward OOS return is -2.93%; only 3/7 folds are profitable.
4. Parameters switch in every fold and local sensitivity is not a broad plateau.
5. The strongest fold has a single trade, while benchmark and cost claims are not
   fully auditable.

No future function or accounting formula error was detected, but those positives
cannot override explicit FAIL conditions. **Stop optimizing this strategy and
replace it. Do not use it alone in live or paper trading.** It should not be kept
as a portfolio candidate until a uniform, immutable data set and a fully retained
audit trail produce a new, independently specified experiment. The unresolved
items are corporate-action verification, uniform adjustment data, original PR #9
artifacts, per-fold benchmarks, daily attribution snapshots, and same-order-path
cost replay.
