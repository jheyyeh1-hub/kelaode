# TSMOM real-market validation report (2026-07-19)

## 1. Executive conclusion

* `execution_status = FAILED_DATA_INTEGRITY`
* `strategy_judgment = FAIL`
* `judgment_status = PROVISIONAL`
* `diagnostic_status = INCOMPLETE`

The preregistered execution stopped at the mandatory immutable-snapshot gate. The committed
manifest was present and had the required hash, but all nine CSV payloads referenced by it were
absent from `data/snapshots/sit-20260719`. The validator's first error was
`snapshot file missing: 510300.csv`. In accordance with the frozen protocol, no experiment command,
candidate evaluation, frozen test, walk-forward run, cost analysis, formal result audit, network
request, redownload, provider substitution, or SIT rerun was performed. The persisted `FAIL` is the
result of `evaluate_tsmom_judgment(...)` with failed audit/accounting inputs, not a discretionary
performance judgment.

This is a research-validation failure report, not an investment recommendation.

## 2. Preregistration anchor

Before snapshot validation, the latest merged-main commit was
`3aa82b4e687534bc8d0c56e1d7a3b40a7038b8f5` (squash-merged PR #17). The package source-tree
fingerprint was `47ddc777143f08cf1eb00ac7e6f5cbd1f411b898507054148724f86554a9967f`.

## 3. Frozen file hashes

| Frozen file | SHA-256 |
|---|---|
| `docs/validation/tsmom_real_market_protocol.md` | `90fb770c00a986ee31742472275f9fb8dbc695d9a489c2fdf6cd36ad4c7552cf` |
| `configs/validation/tsmom_real_market_fixed.json` | `d544ac428f3f81dd09f4b82728c865d76a5cc1cdb123afb81dbd86e0ae281781` |
| `configs/validation/tsmom_real_market_walk_forward.json` | `3bfe2bdac16e02e607fde262a91d512961be957f62a4f5268dc92c3cf944670e` |
| `configs/validation/tsmom_validation_policy.json` | `2fc1fa370fe1750195a66879211de1e40198db5b695442900cb286febdce9561` |

No frozen file was changed after this anchor was recorded.

## 4. Snapshot identity and integrity result

* Manifest: `data/snapshots/sit-20260719/manifest.json`.
* Manifest file SHA-256: `682e83a62e8acc3d4ef3a45c32174a6eff2e668df101bb12e02104d847141643`.
* Canonical snapshot identity: `16ecae299c7944302c0bffe3688bf9bdb2b931012a82d3dd47c79c36778fabfe`.
* Manifest metadata names exactly nine symbols and uniformly claims provider `AKShare/Eastmoney`,
  endpoint `fund_etf_hist_em`, adjustment `qfq`, and requested dates 2005-01-01 through 2026-07-17.
* On-disk validation failed because the nine referenced CSVs were missing. Their content hashes,
  schemas, row counts, and modification state therefore could not be validated.
* Network access used by this attempt: **none**. Redownloads: **none**.

## 5. Fixed selection and selected candidate

Not run. Fixed experiment ID, candidate IDs, eligibility table, ranking evidence, selected
parameters, and frozen-test child ID are unavailable. Candidates evaluated: **0**, not 72.

## 6. Frozen-test metrics and benchmark comparison

Unavailable. No frozen-test or corrected invested-benchmark artifact exists for this attempt; no
metric has been inferred or copied from another run.

## 7. Walk-forward results and parameter stability

Not run. Walk-forward experiment ID, folds, boundaries, selections, stitched OOS metrics,
drawdown, switching frequency, and independent stitched-equity reconstruction are unavailable.

## 8. Cost analysis

Not run. Closed-loop scenarios, fixed-fill-path repricing, fill-path SHA,
`moderate_equity_ratio`, and `severe_equity_ratio` are unavailable.

## 9. Drawdown, concentration, contribution, and sensitivity diagnostics

Unavailable. No drawdown path, point-in-time trend/volatility diagnostics, cash exposure,
concentration, ETF/year contribution, or adjacent one-factor sensitivity was generated.
Consequently `diagnostic_status = INCOMPLETE`; no conservative values were invented.

## 10. SIT diversification comparison

Unavailable because the primary TSMOM result was never sealed. SIT was not rerun and its frozen
files were not modified.

## 11. Independent audits

Formal selection and TSMOM diagnostic audits were not callable because no result bundle exists.
For mechanical precedence, `audits_pass = false` and `accounting_reconstructable = false`.

## 12. Limitations

The execution environment contains the committed snapshot manifest but not the Git-ignored CSV
payloads. The protocol forbids downloading, substituting, or silently repairing them during this
reuse validation. GitHub CI can test repository code, but cannot turn an absent immutable input
snapshot into valid research evidence.

## 13. Exact mechanical judgment

Every committed threshold and actual (unavailable values are JSON `null`) is persisted in
`tsmom_real_market_judgment_inputs.json`. With `evaluated = true`, `audits_pass = false`, and
`accounting_reconstructable = false`, the committed pure function returns `FAIL` before any absent
performance value is compared. Data/audit/accounting failure has the frozen highest precedence.

## 14. Reproducibility commands

The only formal preflight performed was the repository snapshot validator (offline):

```bash
PYTHONPATH=src python -c 'from kelaode.snapshot import SnapshotManifest; m=SnapshotManifest.load("data/snapshots/sit-20260719/manifest.json"); m.validate("data/snapshots/sit-20260719", expected_symbols=["510300","510500","159915","512100","512880","512480","518880","513100","511010"], allow_mixed_adjustments=False)'
```

After restoring the exact manifest-addressed CSV bytes, the preregistered commands would be:

```bash
PYTHONPATH=src python -m kelaode.experiment_cli grid-search --config configs/validation/tsmom_real_market_fixed.json
PYTHONPATH=src python -m kelaode.experiment_cli walk-forward --config configs/validation/tsmom_real_market_walk_forward.json
```

They were deliberately **not run** in this failed attempt.
