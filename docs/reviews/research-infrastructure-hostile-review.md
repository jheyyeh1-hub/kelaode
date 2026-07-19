# Hostile independent review: research infrastructure PR

This review compares the complete branch with `main`. Passing tests was not treated as evidence that the design was safe. The initial implementation was reviewed for ignored configuration, identity and cache weaknesses, artifact reconstructability, benchmark comparability, unsafe compatibility, and claims not enforced by code.

## Critical findings

1. **The “complete” result bundle contained placeholders and could be reused while partial or corrupted.** `initialize_output` published a directory before artifacts were written, filled required files with `{}` or zero-byte CSVs, and cache reuse checked only `identity.json`. A failed write could therefore become a permanent valid-looking cache. **Fixed:** the shared runner writes every contracted artifact with an explicit schema into a temporary directory, hashes every artifact, atomically renames only after success, and verifies identity, membership, and every artifact hash on reuse. The unsafe placeholder API now fails explicitly.
2. **Several authoritative configuration fields were ignored.** The runner ignored portfolio constructor parameters, split mode, data alignment, benchmark definitions, the legacy benchmark field, and CLI command semantics; category merging also allowed one cost setting to silently override another. **Fixed:** unsupported constructors and split execution fail, CLI exposes only implemented `run`, intersection/union alignment is applied, parameter categories have disjoint allowlists, legacy benchmark is rejected, execution timing is explicit, and configured benchmarks are actually run.
3. **The advertised experiment ID was only a configuration hash.** Callers could use `ExperimentConfig.experiment_id` without data or code provenance, and uncommitted package source changes were not represented by Git `HEAD`. **Fixed:** the config-only property fails, a separately named configuration fingerprint remains available, and complete identity includes the package source-tree hash in addition to Git, package, Python, dependencies, configuration, and snapshot hashes.
4. **Accounting artifacts could not reconstruct each day.** Fills lacked dates, target weights omitted zero targets, no daily valuation marks were stored, and the rejection artifact omitted lot-rounding rejections. **Fixed:** dated generated/validated/rejected/fill records, complete symbol-by-date targets, daily carried marks, cash, positions, exposure, and an enforced daily accounting identity are persisted.
5. **Benchmark claims were not backed by benchmark execution.** Empty placeholders existed although benchmark configuration was ignored, so dates, capital, fees, and timing could not be compared. **Fixed:** a configured benchmark uses the same aligned data, engine configuration, initial capital, fees, slippage, and next-open timing; exact date equality is asserted. Absence is represented explicitly, not by an unexplained empty metric object.

## High findings

1. **Snapshot paths and numeric values were insufficiently adversarially validated.** A relative path could traverse `data_root`, NaN/Infinity could evade comparisons, dates could be unordered, and CSV schemas were not exact. **Fixed:** path containment, finite values, nonnegative volume, ordered/unique ISO dates, timezone-aware download timestamps, hexadecimal hashes, and exact columns are validated before evaluation.
2. **Fixed-path replay used the last transaction price as a terminal valuation.** This silently changed valuation semantics and could not value an open path independently. **Fixed:** open positions require explicit frozen terminal marks; closed paths need none.
3. **Walk-forward output discarded the complete selection table.** Only the winning validation row survived, preventing an audit of selection and tie-breaking. **Fixed:** every candidate, metrics, error, and cache marker is persisted in each fold result; test remains a separate once-only callback after selection.
4. **Unsafe backward compatibility remained available.** The legacy output initializer and config-only ID preserved precisely the silent behavior schema 2.0 was intended to remove. **Fixed:** both unsafe paths now fail with migration guidance rather than manufacturing plausible artifacts or IDs.

## Medium findings

1. The generic runner intentionally supports only registered daily shared strategies and no-fit runs. Fixed/rolling/expanding primitives are honest and auditable, but a generic artifact adapter for those modes remains future work; unsupported use now fails rather than falling back.
2. The result bundle uses CSV and JSON only. Parquet snapshots can still be downloaded by the data layer, but the immutable experiment validator intentionally accepts CSV only and reports that limitation.
3. Dependency provenance covers the packages relevant to this repository’s current optional research stack rather than every installed distribution. Python, project version, Git SHA, and all package source bytes are covered.

## Low findings

1. Runtime duration is necessarily nondeterministic metadata. Exact reruns reuse the already verified immutable bundle rather than rewriting a different duration under the same identity.
2. Configuration paths and notes are included in the identity even when they may not change mathematical results. This is conservative and can create a new identity unnecessarily, but cannot cause stale reuse.

## Regression evidence added

New tests mutate a cached artifact, attempt publication after a failing strategy lookup, change source provenance, attempt cross-category cost overrides, require explicit marks for open fixed paths, reconstruct accounting on every day, assert benchmark calendar/capital/identity costs, reject ignored constructors and split modes, and verify the complete walk-forward selection table without test-based selection.

## Test-independence follow-up

A separate black-box suite now imports only public APIs and derives expectations without production serialization, accounting, or contract helpers. It independently computes the canonical SHA-256, exact fixed-path cash arithmetic, benchmark fill accounting, daily marked equity, and every bundle file hash. It invokes the real CLI in a subprocess and mutates future bars rather than mocking internals.

This review exposed one additional **critical** isolation defect: the validation callback received the complete `Fold`, including test dates. Separate callbacks therefore did not make test access “structurally impossible” as documented. The selection callback now receives an immutable `SelectionFold` with only train, validation, and warm-up dates; the full fold is provided only after selection to the once-only test callback. A black-box regression asserts the validation view has no `test` attribute and that deliberately favorable test metrics cannot alter selection.

## Merge-readiness follow-up against current main

The complete diff was re-read after fetching `main` at `b06037a`. The branch has no PR #9/#10 ancestry and contains no new SIT implementation; the historical PR #8 SIT configuration is isolated under `configs/legacy/` and explicitly non-runnable.

Required defects found and fixed in this pass:

* **High — union pre-listing crash/forward-fill ambiguity:** mark generation indexed a missing last price. Union bundles now encode pre-listing marks as `available=False` with an empty close, accept zero holdings, reject nonzero unmarked holdings, and reconstruct equity using available point-in-time marks only.
* **High — generic benchmark label hid one hard-coded implementation:** benchmark definitions now use exact tagged variants (`none`, `single_symbol_buy_and_hold`, or `equal_weight_buy_and_hold`), reject extra/type-specific fields, enforce capital/timing, execute only the named behavior, and persist the resolved definition.
* **High — primary metrics silently omitted execution facts:** runner metrics now reconcile execution count, realized exits, commissions, traded notional, turnover, orders, rejections, and exposure with saved artifacts. Exit-dependent statistics are null when no realized exit exists rather than invented as zero.
* **High — fixed-path replay admitted impossible long-only paths and non-finite inputs:** negative cash, oversells, empty symbols, missing marks, non-finite cash/costs/prices, and invalid final positions now fail explicitly; short paths require an explicit opt-in.
* **Medium — old CLI names disappeared:** `grid-search` and `walk-forward` remain discoverable but fail with a schema-2.0 migration message until complete validated runners exist.
* **Medium — maintained/legacy configuration ambiguity:** every JSON directly under `configs/` loads as schema 2.0; the unmigrated historical SIT draft is documented under `configs/legacy/`. Snapshot schema/example files were also synchronized with the immutable manifest contract.
* **Medium — no repository CI:** a Python 3.11 GitHub Actions workflow now installs the package and runs full pytest, compile, dependency, and import checks on pull requests and pushes to main.

Independent regressions cover staggered listing dates, missing-mark encoding and accounting, typed benchmark rejection/alignment, metrics-to-CSV reconciliation, legacy CLI errors, maintained-config loading, and adversarial fixed-path inputs. CI status is an external GitHub condition and is not claimed passing until the workflow appears and completes on the pushed head.
