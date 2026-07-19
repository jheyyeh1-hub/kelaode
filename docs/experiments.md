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
Schema-2.0 selection currently accepts registered fixed-rule/no-fit strategies
only. Train boundaries are retained as unused lineage and every evaluation says
`fit_applicable: false`; the runner does not claim training occurred. Registered
fittable strategies are rejected until fitted-state isolation and contracts exist.

The required `history_only` warm-up policy supplies bars to indicator history
but disables order queuing and execution until validation/test begins. Warm-up
therefore creates no fills, costs, or positions, and eligible-period equity starts
at configured initial capital. The resolved execution boundary and warm-up policy
are identity-bearing child configuration fields.

The CLI exposes `run`, `grid-search`, and `walk-forward`; each dispatches to the
same validated Python API described below. The runners write and hash their
CSV/JSON artifacts. A configured benchmark is rerun with identical dates,
capital, costs, and next-open execution timing.

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
CSV snapshots. It does not provide a live broker, minute data, multiprocessing,
or an application-specific strategy factory.

## Schema 2.0 experiment modes

`experiment_mode` is mandatory in JSON and is independent of the strategy name:

* `run` executes one predeclared, no-fit configuration.
* `fixed_selection` enumerates the finite `parameter_selection.parameter_grid`, uses only the
  explicit validation interval to rank it, freezes the winner, and evaluates the frozen test
  interval once.
* `walk_forward` applies the same validation-only selection API to explicit rolling or
  expanding folds. Test observations are OOS, ordered, unique, and never enter selection.

Both selection modes require deterministic tie breaks, direction, constraints, minimum
observations, failure policy, and serial resource limits. The product of grid dimensions is
checked against `maximum_candidate_count` before snapshot data is loaded. Walk-forward also
requires `maximum_folds`; an optional wall-clock budget is checked between candidates.
Candidates are checkpointed immediately and resumed only after their manifests and exact
canonical identities validate. There is no network provider or automatic grid expansion.

Run the maintained synthetic examples with:

```bash
PYTHONPATH=src python -m kelaode.experiment_cli run --config configs/sit_synthetic.json
PYTHONPATH=src python -m kelaode.experiment_cli grid-search --config configs/sit_synthetic_fixed.json
PYTHONPATH=src python -m kelaode.experiment_cli walk-forward --config configs/sit_synthetic_walk_forward.json
```

A parent directory contains resolved configuration, identity, runtime provenance, result
contract, and artifact manifest. Candidate directories contain their complete identity,
configuration and evaluation plus a sealed shared-runner bundle. Fixed tests and every fold
likewise retain shared-runner equity, cash, positions, weights, orders, fills, trades, and
benchmark artifacts. Walk-forward parents add stitched test-only equity/drawdown and fold and
parameter-switch histories. Missing, changed, or undeclared files invalidate reuse.

Each manifest declares its direct files and hashes, exact child paths and child-manifest hashes,
schema version, and expected candidate/fold counts. Validation rejects missing, additional,
partial, or changed children and altered nested files. Incomplete candidate, test, scenario,
fold, stitched, or parent publication is either removed and recomputed or rebuilt from already
validated candidate checkpoints; incomplete directories are never cache hits.

Fold-local OOS equity is saved unchanged in `fold_local_equity.csv`. Stitching anchors the first
observation of each independently capitalized fold to the prior stitched ending capital, then
compounds that fold's local equity ratios. A one-observation fold contributes zero return;
nonpositive/nonfinite anchors and unordered or duplicate dates are rejected. The corresponding
rebased segment is saved as `fold_rebased_equity.csv`, while the parent saves continuous
`stitched_oos_equity.csv` and drawdown. Independent fold initial cash is never treated as a cash
flow or artificial drawdown.

Closed-loop scenarios rerun only the already-selected parameters under new execution costs.
Fixed-path scenarios instead reprice the identical base fill quantities and ordering. They are
stored under separately labelled result keys; neither causes a grid rerun unless a future,
explicit diagnostic sensitivity mode is implemented.

These fixtures are infrastructure checks, not performance evidence. Real SIT validation remains
pending. Outputs from obsolete PR #9 and PR #10 remain obsolete and must not be migrated or used.
