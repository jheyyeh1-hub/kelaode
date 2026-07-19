# Reproducible experiments

The canonical workflow and artifact lineage are defined in [ADR 0001](adr/0001-canonical-research-workflow.md). Configuration schema 2.0 is strict and authoritative; the runner validates the immutable data manifest before strategy construction.

Run the offline deterministic example from the repository root:

```bash
python -m kelaode.experiment_cli run --config configs/synthetic_example.json
```

Real input files should be retained externally under a content-addressed, read-only snapshot location. Commit or archive the manifest with experiment artifacts, mount the unchanged files at `data_root`, and verify their SHA-256 values before every run. Never regenerate a file beneath an existing manifest.

`ExperimentConfig` is the immutable experiment specification. Its ID is a
canonical JSON hash, so mapping and JSON field order cannot alter identity.
Metadata ties results to the Git revision, runtime, dependency versions and a
hashed data manifest.

`FixedSplit`, `RollingWalkForward`, and `ExpandingWalkForward` consume an
explicit trading calendar. A fold keeps train, validation and test disjoint.
Warm-up dates precede test dates and are context only: callers must slice
reported returns at the first test date. Grid search callbacks may receive only
train and validation views; invoke the existing backtest engine on test only
after `GridSearch.select` freezes parameters. Fittable strategies persist their
JSON-compatible state between these two operations; ordinary strategies remain
unchanged.

```bash
python -m kelaode.experiment_cli run --config configs/example_momentum.json
python -m kelaode.experiment_cli grid-search --config configs/example_momentum.json
python -m kelaode.experiment_cli walk-forward --config configs/example_momentum.json
```

The CLI establishes the versioned output contract. Application integrations
populate its CSV/JSON artifacts using their existing data loader and backtest
engine. Benchmark utilities align by common valuation date and report active
risk. Cost scenarios are parameter overrides and do not modify execution
semantics. Parallelism is deliberately single-process by default.

## Limitations

The generic layer cannot instantiate application-specific strategy classes or
data sources by name. Integrations supply those factories and their data
manifest. PNG placeholders are empty if matplotlib is not installed.
