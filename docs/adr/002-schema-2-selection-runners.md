# ADR 002: canonical schema-2.0 selection runners

**Status:** accepted

Selection orchestration extends the shared immutable snapshot, identity, registry and
`run_experiment` execution bundle. It does not create strategy-specific pipelines. Validated
configuration is authoritative for grids, dates, costs, benchmarks, resource limits, and output.

A candidate identity hashes the parent identity, full parameters, every manifest input hash,
train/validation/warm-up boundaries, fees, slippage, execution, constraints, benchmark, strategy
class, source digest, Git SHA, Python/package/dependency provenance, and candidate schema version.
Indices and abbreviated parameter strings are never identities. Exact identity and artifact
manifest validation is required for reuse.

The selection callback receives no test boundary. Deterministic validation ranking freezes one
parameter mapping before a single test run. Walk-forward repeats this contract per fold and
stitches only test dates, rejecting OOS overlap. Serial execution, finite-grid and fold caps,
between-candidate time checks, checkpointing, and explicit failure policy bound resource use.
No live data source is part of this architecture.
