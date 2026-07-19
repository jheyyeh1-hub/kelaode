# Legacy configurations

Files in this directory are preserved historical examples and are **not runnable** by the schema 2.0 experiment CLI. The SIT validation draft predates immutable snapshot manifests and remains here only to document the configuration merged in PR #8; it was not migrated, executed, or used as a base for the shared infrastructure work.

Maintained runnable/configuration-validated examples live directly under `configs/` and must load with `ExperimentConfig.from_json`.
