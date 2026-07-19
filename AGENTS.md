# Repository development policy

* Read the existing architecture before adding an experiment runner. Extend the shared abstractions instead of creating strategy-specific duplicate pipelines.
* Treat validated configuration as authoritative; never duplicate result-affecting constants in runners.
* Never claim investment performance without immutable input snapshots and the complete result artifacts.
* Never mix adjustment conventions in an investment-valid experiment. Diagnostic exceptions must be explicit.
* Avoid result-driven grid expansion and keep strategy implementation, validation, and independent audit as separate tasks.
* Run the complete test suite and report unresolved limitations.
* Avoid modifying unrelated files. Never silently swallow exceptions or fall back to another provider or adjustment convention.
