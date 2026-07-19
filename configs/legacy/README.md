# Legacy configurations

Files in this directory are preserved historical examples and are **not runnable** by the schema 2.0 experiment CLI. The SIT v1 validation draft predates immutable snapshot manifests and remains here only as history; it was not migrated, executed, or used as a base for the shared infrastructure work. Obsolete PR #9/#10 validation and audit flows must not be used.

Maintained runnable/configuration-validated examples live directly under `configs/` and must load with `ExperimentConfig.from_json`.
