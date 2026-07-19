# Time-series trend real-market validation protocol (frozen preregistration)

**Status:** `PREREGISTERED — NOT EXECUTED`  
**Freeze date:** 2026-07-19  
**Strategy:** existing `TimeSeriesTrendStrategy` from merged PR #16

This document freezes the first formal real-market validation before parameter selection or result inspection. This change generates no formal fixed-selection, frozen-test, or walk-forward result and makes no economic or investment-performance claim. The frozen SIT protocol, snapshot, reports, results, and judgment are inputs that this work does not modify.

## Research question and separation of duties

The primary question is whether the long-only, multi-asset time-series trend family with inverse-volatility sizing has reproducible out-of-sample economic value—not whether one in-sample parameter set is profitable. The formal run will test positive absolute frozen-test and stitched walk-forward return, drawdown control, positive excess return over the cost-aligned benchmark, walk-forward stability, cost robustness, and useful diversification relative to sealed SIT results. Protocol/implementation, formal execution, and independent audit remain separate tasks. Frozen-test, walk-forward, cost, contribution, sensitivity, and SIT comparison data cannot influence selection.

## Frozen strategy equations

Decision logic is unchanged. For symbol *i* on completed close *t*:

`trend_i(t) = close_i(t) / close_i(t - trend_lookback) - 1`.

Eligibility is strict: `trend_i(t) > signal_buffer`. Realized volatility is the sample standard deviation of the previous `volatility_lookback` close-to-close returns, annualized by `sqrt(252)`. The raw score is `1 / realized_volatility_i`; eligible scores are normalized by the strategy-native implementation. If `maximum_active_assets` binds, ordering is stronger positive own trend, then lower realized volatility, then canonical symbol order. No rule may change after real-market results are viewed.

Full-gross normalization is resolved by `max_single_weight = 1.0`: a sole eligible asset may receive the full signal allocation. There is no clipping or capped renormalization. Concentration is governed by `maximum_active_assets`, diagnostics, and mechanical judgment—not a post-signal cap.

## Immutable universe and data policy

| Symbol | Economic class | Official listing date |
|---|---|---|
| 510300 | China large-cap equity (CSI 300) | 2012-05-28 |
| 510500 | China mid/small-cap equity (CSI 500) | 2013-03-15 |
| 159915 | China growth equity (ChiNext) | 2011-12-09 |
| 512100 | China small-cap equity (CSI 1000) | 2017-08-25 |
| 512880 | China securities-sector equity | 2016-08-08 |
| 512480 | China semiconductor-sector equity | 2019-06-12 |
| 518880 | Gold commodity | 2013-07-29 |
| 513100 | US large-cap technology/growth equity (NASDAQ-100) | 2013-05-15 |
| 511010 | China government bonds | 2013-03-25 |

No symbol may be added, removed, replaced, or substituted. Data are frozen to provider `AKShare/Eastmoney`, endpoint `fund_etf_hist_em`, daily frequency, uniform `qfq`, requested 2005-01-01 through 2026-07-17. There is no alternate endpoint, fallback provider, mixed adjustment, or symbol substitution.

The existing SIT snapshot identity is `data/snapshots/sit-20260719/manifest.json` (manifest SHA-256 is committed in `tsmom_validation_policy.json`). Reuse is allowed only if all nine symbols exactly match, every CSV hash matches the manifest, every entry is successful and uniformly qfq from the frozen provider/endpoint/range, and no manifest or snapshot byte changed. A mismatch aborts; it never triggers download. Redownload solely to obtain a different revision is prohibited.

## Calendar, splits, and warm-up

Common usable period: **2019-06-12 through 2026-07-17**, using actual exchange sessions after filtering.

* Train: 2019-06-12–2021-12-31.
* Validation: 2022-01-01–2023-12-31.
* Frozen test: 2024-01-01–2026-07-17.

The formal configs use `history_only` and 253 observations, sufficient for the maximum 252-session trend calculation including its prior close and the 63-return volatility history. Warm-up may compute signals but cannot create orders, fills, positions, or benchmark positions. Execution begins only at each derived formal boundary. Boundaries cannot change after parameter inspection.

## Frozen candidate grid (72 candidates)

The exact Cartesian grid is:

* `trend_lookback`: 63, 126, 252
* `volatility_lookback`: 21, 63
* `rebalance_frequency`: 5, 21 sessions
* `signal_buffer`: 0.00, 0.02
* `maximum_active_assets`: null, 3, 5

Thus `3 × 2 × 2 × 2 × 3 = 72`. All values satisfy the strategy's positive-integer, minimum-volatility-history, nonnegative-buffer, and universe-cap rules; 72 serial candidates are within the committed resource limit. There are no hidden parameters and the grid will not expand after results.

## Portfolio and execution freeze

`initial_cash=100000`, long-only true, leverage false, `max_gross_exposure=1.0`, `cash_buffer=0.01`, `max_single_weight=1.0`, `lot_size=100`, participation rate 0.10, partial-fill policy `partial_fill`, next-open execution. Base cost is commission 0.00025, minimum 5, slippage 0.0005; moderate is 0.00050, 5, 0.0010; severe is 0.00100, 10, 0.0020. Each scenario requires both a closed-loop rerun and fixed frozen-fill-path repricing. Increasing costs must not improve final equity; violation fails audit.

## Benchmark

The sole primary benchmark is corrected, cost-aligned equal-weight buy-and-hold of the same nine ETFs, using the audited post-PR-15 shared implementation. It begins only after execution eligibility, emits exactly one equal-weight target, executes next open with actual orders/fills/positions, then holds. It uses identical capital, lot, participation, commissions, and slippage. Cash and sealed SIT are descriptive only.

## Fixed selection objective

Selection sees validation data only and maximizes validation Sharpe. Eligibility requires validation total return > 0, maximum drawdown >= -0.35, at least 252 validation observations, and successful artifact/accounting processing. Tie-breaks, in order, are: higher (less negative) maximum drawdown; lower turnover; lower mean rebalance-date target-weight HHI concentration; longer trend lookback; longer rebalance frequency; canonical parameter serialization. Exactly one selected candidate may enter frozen test. Test information is unavailable to selection.

## Rolling walk-forward

The frozen schedule uses rolling 504-session train, 252-session validation, 126-session test, and 126-session step, with 253 history-only warm-up observations. Every fold uses the same grid, objective, constraints, and tie-breaks; train and validation alone select exactly one test child. Test dates cannot overlap. Stitched OOS equity, drawdown, and metrics must be reconstructed independently from fold-local paths.

## Required artifacts and audit

Persist total return, CAGR, annualized volatility, Sharpe, Sortino, Calmar, maximum drawdown and duration, turnover, traded notional, commissions, fill count, excess total return, tracking error, information ratio, beta, alpha, correlation, and active drawdown.

Persist point-in-time trend, realized volatility, eligibility reason, raw inverse-volatility score, normalized weight, rebalance flag, active count, concentration, calendar-year and ETF contribution, time in cash, simultaneous active assets, and walk-forward parameter-selection frequency. The independent auditor must verify snapshot/config/candidate/fold identities; selected-only tests; diagnostic equations/ranking/normalization; benchmark timing and holdings; accounting, fills/trades, turnover and costs; stitched OOS; contributions; and diagnostic completeness.

After the primary TSMOM result is sealed, descriptive SIT comparison may read **only already sealed official SIT artifacts**: daily-return correlation, correlation during SIT drawdowns, TSMOM return during SIT's worst drawdown, equal-risk combination diagnostics, and combined maximum-drawdown change. SIT is never rerun, and these diagnostics cannot alter TSMOM selection or judgment.

## Mechanical judgment and failure handling

`configs/validation/tsmom_validation_policy.json` and the pure `evaluate_tsmom_judgment` function are authoritative. Precedence is FAIL, then PASS, otherwise CONDITIONAL.

**FAIL** if an audit fails; protocol changed after results; frozen-test return <= -20%; stitched OOS return <= -20%; stitched maximum drawdown < -50%; moderate-cost final-equity/base-equity ratio < 90%; or execution/accounting is not independently reconstructable.

**PASS** requires frozen-test return > 0; frozen-test excess return > 0; stitched OOS return > 0; stitched maximum drawdown >= -35%; moderate ratio >= 95%; audits pass; diagnostics complete; maximum positive contribution share by a single ETF or calendar year <= 60%; and no broad adjacent one-factor sign reversal or parameter instability.

**CONDITIONAL** applies when no FAIL condition holds but any PASS requirement fails, including nonpositive excess despite positive absolute return, severe-cost ratio < 90%, contribution above 60%, incomplete diagnostics, instability, or adjacent-parameter sign reversal. Judgment is never assigned manually.

Any missing/mutated data, identity, selected-only isolation, benchmark, accounting, reconstruction, or sealed-SIT requirement aborts formal processing and records an audit failure; no provider/adjustment fallback is allowed. Candidate-level computational failures follow the recorded `continue` policy and remain explicit, but an incomplete required audit prevents PASS.

## Execution embargo

This PR freezes protocol and audit preparation only. Formal parameter selection, frozen testing, walk-forward validation, SIT reruns, and economic-result publication are expressly out of scope.
