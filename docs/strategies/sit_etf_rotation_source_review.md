# SIT ETF rotation: source review and migration record

## Pinned source and license

- **Repository:** <https://github.com/SystematicInvestor/SIT>
- **Reviewed commit:** `f9b1185036d9a97764e8c5e12f091b8338926190` (retrieved 2026-07-19).
- **License:** zlib, as declared by `pkg/DESCRIPTION` at the reviewed commit. This
  implementation is a clean Python adaptation of the rule, not a copy of SIT or
  its framework.
- **Original strategy file:** `R/strategy.r`, function `rotation.strategy`
  (approximately lines 409--465 at the pinned commit).

## Original rule

Pseudocode, paraphrased from the pinned function:

1. Load adjusted historical prices for the selected funds.
2. Find period ends (monthly by default).
3. At period ends calculate `price[t] / price[t-126]`.
4. Rank funds by that value and equal-weight the best `top.n` funds.
5. The function also supplies an optional top/keep variant: buy the top funds and
   retain them while their rank remains within `keep.n`.

Defaults are tickers `DIA, SPY, SHY`, monthly periodicity, `top.n = 2`, and
`keep.n = 6`. Inputs are price histories; no order-book or proprietary field is
required. The source delegates trading semantics to SIT's `bt.run.share`. Because
the reviewed function does not itself unambiguously document whether a period-end
signal fills at that close or the next bar, this migration deliberately uses the
more conservative **signal at close, fill at next open** timing.

The function builds model objects but makes no numerical performance claim in its
code or documentation. Accordingly, no third-party return is reported here.

## Migrated and deliberately changed

`SITMomentumRotationStrategy` migrates the 126-session relative-momentum ranking,
top-k selection, equal weighting, and configurable periodic rebalance. It uses a
point-in-time `MarketView` and produces a complete long-only target portfolio for
the existing portfolio engine, `ConstraintEngine`, `FillModel`, and transaction
cost path.

Optional, off-by-default extensions are a close-versus-moving-average trend
filter and inverse-realized-volatility weighting. A listing-age threshold,
current-session tradability check, deterministic tie break, maximum-weight cap,
weekly/daily/fixed-interval schedules, and missing-history exclusion make the rule
safe for a changing Chinese ETF universe. The original top/keep turnover buffer
is **not** migrated. SIT's data downloader, backtester, reporting code, adjusted
price conventions, and its example US universe are also not migrated.

Potential deviations and biases include adjusted-versus-unadjusted close data,
Chinese ETF lot sizes and price limits, survivorship in a hand-selected present-day
universe, calendar differences, next-open execution, stale/suspended instruments,
minimum commissions, and different corporate-action handling. Listing filters
must use only age observable on each signal date; pre-listing values must never be
backfilled.

## Reproducible validation plan and current status

The checked-in experiment template uses `510300, 510500, 159915, 512100, 512880,
512480, 518880, 513100, 511010`. After downloading data, record each symbol's first
and last valid date and derive train/validation/test boundaries from their actual
union coverage. Use listing age 127 or greater. Compare 510300 buy-and-hold,
universe equal-weight buy-and-hold, and the existing
`CrossSectionalMomentumStrategy`.

The intentionally small search is momentum lookback `{63, 126, 189}`, top-k
`{1, 2, 3}`, rebalance `{monthly, weekly}`, optional trend window `{null, 100,
200}`, volatility lookback `{null, 20, 60}`, and max weight `{0.34, 0.5, 1.0}`;
invalid top-k/max-weight combinations should be rejected before execution. Select
only on validation, lock the choice for test, and report every rolling fold's
choice plus dispersion. Repeat test execution at base costs and at 2x/4x
commission and slippage.

No real market history is committed. An AKShare/Eastmoney download was attempted
on 2026-07-19 and succeeded for all nine symbols: their observed starts were
2012-05-28, 2013-03-15, 2011-12-09, 2016-11-04, 2016-08-08, 2019-06-12,
2013-07-29, 2013-05-15, and 2013-03-25 respectively (in universe order), and all
ended 2026-07-17. Thus the common unfiltered coverage starts 2019-06-12. The
downloaded files remained outside the repository. The full grid run exceeded the
available process resources before producing a result, so no full historical run
was completed in this change. Therefore there are currently **no** train, validation, out-of-sample,
walk-forward, drawdown, turnover, or cost-sensitivity figures to report. The unit
fixture is synthetic and tests mechanics only; it is not investment performance.
