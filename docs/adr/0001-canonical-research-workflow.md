# ADR 0001: Canonical research workflow and artifact lineage

**Status:** Accepted (experiment schema 2.0)

## Decision

Every experiment follows one shared lineage:

1. A strict JSON configuration names the ordered universe, immutable snapshot manifest, strategy and complete parameters, portfolio constructor, constraints, capital, costs, execution, benchmark, splits, and output root.
2. `SnapshotManifest.validate` verifies source metadata, adjustment consistency, file existence, exact SHA-256, row counts, dates, uniqueness, and OHLC invariants **before** a strategy is instantiated.
3. The identity hashes the complete resolved configuration, manifest and each file hash, package source-tree hash, package and dependency versions, Python version, Git commit, and schema version. A matching directory is reusable only when its persisted identity and every artifact hash are exact.
4. The shared runner performs the backtest and atomically publishes stable JSON/CSV audit artifacts plus an artifact manifest. Configuration, identity, input manifest, daily marks, and runtime provenance connect every output to its inputs. Charts are optional derived presentation, never empty contract placeholders.
5. Validation selects parameters without test access. Parameters are frozen before each test callback; fold boundaries, the full selection table, validation metrics, test metrics, and OOS curves belong in the result bundle. A non-fittable strategy describes its split as evaluation, not training.

## Cost semantics

**Closed-loop cost stress** reruns the engine with alternate costs. Affordability, lot rounding, fills, subsequent signals, and holdings may differ. It is robustness analysis and must not be presented as same-path monotonicity evidence.

**Fixed-path cost replay** freezes symbols, sides, quantities, order, and reference-price path, then changes only commission and slippage accounting. With nonnegative costs, increasing either cannot improve terminal equity. Reports label these modes separately.

## Snapshot retention

Large market files stay outside Git, for example in read-only object storage under `snapshots/<manifest-hash>/`. Retain the small manifest in the experiment record and copy files without transformation. On restoration, place or mount them at `data_root`; validation recomputes hashes. If a vendor file changes, preserve it as a new snapshot and identity rather than overwriting the old object. Synthetic CSV fixtures in `tests/fixtures/snapshot` are intentionally small and deterministic.

## Migration

Version 1 configurations must be migrated explicitly: add `schema_version`, `data_manifest`, `data_root`, `slippage_parameters`, `benchmark_definitions`, and `split_definitions`. CLI commands now run the validated configuration instead of merely creating empty files. Unknown keys and unsupported engine parameters are errors. Existing unversioned result folders are not cache-compatible and should remain archived rather than renamed.

## Boundaries and limitations

The built-in runner currently registers only shared daily strategies, CSV snapshots, no-fit runs, and the daily portfolio engine. It does not download data, provide a live broker, handle minute bars, or infer vendor conventions. Configured benchmarks run with the same capital, daily calendar, costs, and next-open timing. A generic parameter-search artifact adapter remains an explicit future extension rather than a silent approximation.
