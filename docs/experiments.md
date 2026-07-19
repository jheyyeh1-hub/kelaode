# Reproducible experiments

The canonical workflow and artifact lineage are defined in [ADR 0001](adr/0001-canonical-research-workflow.md). Configuration schema 2.0 is strict and authoritative; the runner validates the immutable data manifest before strategy construction.

Run the offline deterministic example from the repository root:

```bash
python -m kelaode.experiment_cli run --config configs/synthetic_example.json
```

Real input files should be retained externally under a content-addressed, read-only snapshot location. Commit or archive the manifest with experiment artifacts, mount the unchanged files at `data_root`, and verify their SHA-256 values before every run. Never regenerate a file beneath an existing manifest.

`ExperimentConfig` is the immutable experiment specification. A configuration
has only a configuration fingerprint, not an experiment ID. The complete ID is
created only after the validated snapshot, package source bytes, Git revision,
runtime and dependency versions are available.

`FixedSplit`, `RollingWalkForward`, and `ExpandingWalkForward` consume an
explicit trading calendar. A fold keeps train, validation and test disjoint.
Warm-up dates precede test dates and are context only: callers must slice
reported returns at the first test date. Grid search callbacks may receive only
train and validation views; invoke the existing backtest engine on test only
after `GridSearch.select` freezes parameters. Fittable strategies persist their
JSON-compatible state between these two operations; ordinary strategies remain
unchanged.

The CLI implements only the no-fit `run` workflow. The historical `grid-search`
and `walk-forward` command names remain discoverable for compatibility, but
exit with a schema-2.0 migration error rather than running an unsafe placeholder.
Fixed split and walk-forward callers use the explicit selection APIs and must
persist their returned boundaries and complete selection tables through an
integration; the CLI never pretends that a split was executed. The runner
atomically writes and hashes its CSV/JSON artifacts. A configured benchmark is
rerun with identical dates, capital, costs, and next-open execution timing.

`benchmark_definitions.type` must be `none`, `single_symbol_buy_and_hold`, or
`equal_weight_buy_and_hold`; no arbitrary benchmark strategy is implied. The
resolved definition is persisted in `resolved_benchmark.json`.

Under `union` alignment, `marks.csv` contains one row per date and symbol with
an `available` boolean. Before listing, `available` is false and `close` is
empty—prices are never backfilled from the future. Zero holdings need no mark;
a nonzero holding without a current or previously observed close aborts the run.

Maintained schema-2.0 configurations are the JSON files directly under
`configs/`. Historical files under `configs/legacy/` are documentation only and
must not be passed to the experiment CLI.

## Limitations

The generic runner instantiates only its registered shared daily strategies and
CSV snapshots. It does not currently provide a generic split/grid artifact
adapter, live broker, minute data, or application-specific strategy factory.
