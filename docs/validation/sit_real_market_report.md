# SIT real-market validation report (2026-07-19)

## 1. Executive conclusion

**Judgment: FAIL — infrastructure/data acquisition failure; strategy performance was not
evaluated.** The frozen primary AKShare/Eastmoney `fund_etf_hist_em` endpoint did not return a
valid response for 518880 after the command's two same-provider retries. In accordance with the
precommitted protocol, the incomplete staging snapshot was rejected and neither fixed-selection
nor walk-forward validation was started. There is therefore no basis to call the strategy
profitable, successful, validated, or deployable.

## 2. Frozen judgment

The protocol makes any data audit failure a FAIL and requires stopping rather than substituting a
provider, adjustment, or symbol. This report applies that rule mechanically. It is an experiment
execution failure, **not** evidence for or against the economic strategy.

## 3. Frozen protocol

The controlling protocol is [`sit_real_market_protocol.md`](sit_real_market_protocol.md), committed
as `aed3138` before any final metrics were inspected. It was not amended after acquisition output.
The universe, qfq convention, dates, 16 candidates, validation-Sharpe objective, -35% eligibility
constraint, tie breaks, costs, and decision thresholds remain exactly as frozen.

## 4. Data provenance

The attempted source was AKShare/Eastmoney, endpoint `fund_etf_hist_em`, daily qfq OHLCV, requested
2005-01-01--2026-07-17. The complete nine-symbol operation began at
2026-07-19T09:57:13Z. Eight responses passed normalization, but 518880 failed with
`JSONDecodeError: Expecting value: line 1 column 1 (char 0)` at 2026-07-19T09:57:25Z after the
configured retries. A direct same-endpoint diagnostic at approximately 09:57:35Z reproduced the
same error. No fallback was attempted and no symbol was substituted or removed.

Because the freeze failed, partial row counts/hashes are staging diagnostics only and are neither
an immutable snapshot nor investment evidence. Raw staging CSVs and the invalid staging manifest
remain Git-ignored.

## 5. Snapshot audit

**Failed:** the manifest contains a failed entry and therefore cannot be loaded as an immutable
snapshot. Adjustment consistency, hashes, OHLC/volume, duplicates, monotonicity, missing dates,
jumps, and listing dates cannot collectively pass until all nine entries exist. No valid snapshot
identity was published.

## 6. Fixed-selection results

Not run. Candidate table, deterministic ranking and selected candidate ID are N/A. This prevents
test information from leaking through an attempt to work around the acquisition failure.

## 7. Frozen-test results

Not run. Total return, CAGR, annualized volatility, Sharpe, Sortino, Calmar, maximum drawdown,
drawdown duration, turnover, traded notional, commissions, benchmark excess return, tracking
error, information ratio, beta, alpha, correlation and active drawdown are all **N/A**.

## 8. Walk-forward results

Not run. Fold boundaries, selected parameters, OOS metrics, stitched equity/drawdown and parameter
switches are N/A.

## 9. Benchmark comparison

Not run. The frozen primary definition remains cost-aligned equal-weight buy-and-hold of the same
nine ETFs; it was not replaced by a benchmark that could run on only eight files.

## 10. Cost analysis

Not run. Closed-loop base/moderate/severe and fixed-path replay are N/A because there is no selected
strategy or frozen fill path.

## 11. Drawdown analysis

Not run; all strategy and benchmark drawdown values are N/A.

## 12. Contribution and concentration

Not run. These are post-selection diagnostics and cannot precede a sealed primary result.

## 13. Parameter stability

Not run; no walk-forward fold was selected.

## 14. Limitations

* The provider failure prevents the requested first formal real-market validation.
* The committed read-only auditor checks sealed artifact/data hashes recursively, reconstructs
  daily accounting, reconciles trades/fills, enforces next-open and warm-up boundaries, checks
  pre-listing positions, benchmark alignment, selected-candidate membership, OOS disjointness and
  stitched date continuity. Future-data mutation isolation remains covered by runner synthetic
  tests rather than inferred from absent real artifacts.
* No immutable market data or generated result bundle is committed.
* GitHub CI status must be obtained from the pull request; local checks cannot assert remote CI.

## 15. Reproducibility commands

The failed freeze command was:

```bash
PYTHONPATH=src python -m kelaode.data_cli download \
  --symbols 510300,510500,159915,512100,512880,512480,518880,513100,511010 \
  --start 2005-01-01 --end 2026-07-17 \
  --output data/snapshots/sit-20260719 --adjust qfq --retries 2 --format csv
```

Only after a future, wholly successful freeze and a pre-result documented restart may these run:

```bash
PYTHONPATH=src python -m kelaode.experiment_cli grid-search --config configs/validation/sit_real_market_fixed.json
PYTHONPATH=src python -m kelaode.experiment_cli walk-forward --config configs/validation/sit_real_market_walk_forward.json
PYTHONPATH=src python -m kelaode.validation_audit --artifacts <sealed-result-identity-directory>
```

The two experiment commands must each be repeated verbatim to demonstrate exact cache reuse.

## 16. Artifact locations and identities

* Protocol commit: `aed3138`.
* Fixed configuration fingerprint: `d8979b6508f2b44c47e7caec507ada259c9b53e15d42de0fc66bfa9d199345eb`.
* Walk-forward configuration fingerprint:
  `a1fe25e43abafa9c6afe0c20ffe23c917c2e0e0e596917e95292799b7c1867e8`.
* Valid snapshot ID, fixed experiment ID, walk-forward ID, candidate IDs, fold IDs and test-child
  IDs: N/A because publication correctly stopped at the failed freeze.
* Ignored failed staging location: `data/snapshots/sit-20260719/`.

The protocol was not changed after output inspection. Obsolete PR #9/#10 artifacts and code were
not used, copied, merged, or depended upon.
