"""Reproducible, leakage-resistant experiment primitives.

The orchestration layer deliberately accepts a runner callback: it never changes
strategy or backtest semantics and can therefore sit on top of either engine.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import platform
import random
import subprocess
import tomllib
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence
from .snapshot import SnapshotManifest, canonical_json

EXPERIMENT_SCHEMA_VERSION = "2.0"

# Metrics emitted for validation candidate ranking.  Tie-break names are
# validated here, before a manifest is opened or a candidate is executed.
SUPPORTED_VALIDATION_METRICS = frozenset({
    "total_return", "cagr", "annualized_volatility", "sharpe", "sortino",
    "calmar", "max_drawdown", "max_drawdown_duration", "win_rate",
    "profit_factor", "turnover", "gross_exposure", "net_exposure",
    "trade_count", "average_holding_period", "average_trade_return",
    "target_concentration",
})


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class ExperimentConfig:
    experiment_name: str
    universe: tuple[str, ...]
    start_date: str
    end_date: str
    strategy_class: str
    strategy_parameters: Mapping[str, Any] = field(default_factory=dict)
    portfolio_constructor: str = ""
    constructor_parameters: Mapping[str, Any] = field(default_factory=dict)
    initial_cash: float = 100_000.0
    fee_parameters: Mapping[str, Any] = field(default_factory=dict)
    execution_parameters: Mapping[str, Any] = field(default_factory=dict)
    constraint_parameters: Mapping[str, Any] = field(default_factory=dict)
    benchmark: Any = None
    data_alignment_mode: str = "intersection"
    random_seed: int = 0
    output_directory: str = "results"
    notes: str = ""
    schema_version: str = EXPERIMENT_SCHEMA_VERSION
    data_manifest: str = ""
    data_root: str = ""
    slippage_parameters: Mapping[str, Any] = field(default_factory=dict)
    split_definitions: Mapping[str, Any] = field(default_factory=dict)
    benchmark_definitions: Mapping[str, Any] = field(default_factory=dict)
    allow_mixed_adjustments: bool = False
    diagnostic_only: bool = False
    experiment_mode: str = "run"
    parameter_selection: Mapping[str, Any] = field(default_factory=dict)
    resource_limits: Mapping[str, Any] = field(default_factory=dict)
    cost_analysis: Mapping[str, Any] = field(default_factory=dict)
    warmup_policy: str = "none"
    execution_start_date: str | None = None

    def __post_init__(self):
        if self.schema_version != EXPERIMENT_SCHEMA_VERSION:
            raise ValueError(f"unsupported experiment schema: {self.schema_version}")
        if self.initial_cash <= 0 or self.start_date > self.end_date or not self.universe:
            raise ValueError("invalid cash or date range")
        if len(self.universe) != len(set(self.universe)):
            raise ValueError("universe symbols must be unique")
        if self.allow_mixed_adjustments and not self.diagnostic_only:
            raise ValueError("mixed adjustments may only be enabled for diagnostic-only runs")
        if self.experiment_mode not in {"run", "fixed_selection", "walk_forward"}:
            raise ValueError("experiment_mode must be run, fixed_selection, or walk_forward")
        if self.warmup_policy not in {"none", "history_only"}:
            raise ValueError("warmup_policy must be none or history_only")
        if self.execution_start_date is not None:
            try:
                execution_start = date.fromisoformat(self.execution_start_date)
            except (TypeError, ValueError) as exc:
                raise ValueError("execution_start_date must use YYYY-MM-DD") from exc
            if not (date.fromisoformat(self.start_date) <= execution_start <= date.fromisoformat(self.end_date)):
                raise ValueError("execution_start_date must be within the configured interval")
        if not self.data_manifest or not self.data_root:
            raise ValueError("data_manifest and data_root are required")
        try:
            start, end = date.fromisoformat(self.start_date), date.fromisoformat(self.end_date)
        except ValueError as exc:
            raise ValueError("start_date and end_date must use YYYY-MM-DD") from exc
        if start > end:
            raise ValueError("start_date must not follow end_date")
        if self.data_alignment_mode not in {"intersection", "union"}:
            raise ValueError("data_alignment_mode must be intersection or union")
        for name in ("strategy_parameters", "constructor_parameters", "fee_parameters",
                     "slippage_parameters", "execution_parameters", "constraint_parameters",
                     "benchmark_definitions", "split_definitions", "parameter_selection",
                     "resource_limits", "cost_analysis"):
            if not isinstance(getattr(self, name), Mapping):
                raise ValueError(f"{name} must be an object")
        if self.portfolio_constructor != "strategy-native" or self.constructor_parameters:
            raise ValueError("only portfolio_constructor='strategy-native' with no constructor parameters is supported")
        if self.benchmark is not None:
            raise ValueError("legacy benchmark is unsafe; use benchmark_definitions only")
        categories = {
            "fee_parameters": {"commission_rate", "minimum_commission"},
            "slippage_parameters": {"slippage_rate"},
            "execution_parameters": {"execution_timing", "lot_size", "participation_rate", "partial_fill_policy"},
            "constraint_parameters": {"cash_buffer", "max_single_weight", "max_gross_exposure",
                                      "rebalance_tolerance", "max_order_value"},
        }
        for name, allowed in categories.items():
            unknown_parameters = set(getattr(self, name)) - allowed
            if unknown_parameters:
                raise ValueError(f"{name} has unsupported or miscategorized fields: {sorted(unknown_parameters)}")
        if self.execution_parameters.get("execution_timing") != "next_open":
            raise ValueError("execution_parameters.execution_timing='next_open' is required")
        split_type = self.split_definitions.get("type")
        if split_type not in {"none", "fixed", "rolling", "expanding"}:
            raise ValueError("split_definitions.type must be none, fixed, rolling, or expanding")
        if split_type == "none" and not self.split_definitions.get("reason"):
            raise ValueError("a no-fit experiment must document why no split is used")
        self._validate_mode(split_type)
        benchmark_type = self.benchmark_definitions.get("type")
        if benchmark_type == "none":
            if set(self.benchmark_definitions) != {"type"}:
                raise ValueError("benchmark type 'none' accepts only the type field")
        elif benchmark_type in {"equal_weight_buy_and_hold", "single_symbol_buy_and_hold"}:
            symbol_field = "symbols" if benchmark_type == "equal_weight_buy_and_hold" else "symbol"
            required_benchmark = {"type", symbol_field, "capital", "execution_timing"}
            if set(self.benchmark_definitions) != required_benchmark:
                raise ValueError(f"benchmark type {benchmark_type!r} requires exactly {sorted(required_benchmark)}")
            benchmark_symbols = (self.benchmark_definitions[symbol_field]
                if symbol_field == "symbols" else [self.benchmark_definitions[symbol_field]])
            if (not isinstance(benchmark_symbols, list) or not benchmark_symbols or
                    not all(isinstance(symbol, str) for symbol in benchmark_symbols) or
                    not set(benchmark_symbols).issubset(self.universe)):
                raise ValueError("benchmark symbols must be nonempty and drawn from the experiment universe")
            if self.benchmark_definitions["capital"] != self.initial_cash:
                raise ValueError("benchmark and strategy must use identical initial capital")
            if self.benchmark_definitions["execution_timing"] != "next_open":
                raise ValueError("only aligned next_open benchmark execution is supported")
        else:
            raise ValueError("unsupported benchmark type; use none, equal_weight_buy_and_hold, or single_symbol_buy_and_hold")
        # Fail early rather than producing an experiment that cannot be restored.
        _canonical(asdict(self))

    def _validate_mode(self, split_type: str) -> None:
        """Validate orchestration fields before a snapshot is opened."""
        if self.experiment_mode == "run":
            if split_type != "none" or self.parameter_selection or self.resource_limits or self.cost_analysis:
                raise ValueError("run mode requires a no-fit split and no selection-only fields")
            if self.warmup_policy == "history_only" and self.execution_start_date is None:
                raise ValueError("history_only run requires execution_start_date")
            return
        if self.warmup_policy != "history_only" or self.execution_start_date is not None:
            raise ValueError("selection modes require history_only warm-up and derive execution_start_date per child")
        expected = "fixed" if self.experiment_mode == "fixed_selection" else {"rolling", "expanding"}
        if (split_type != expected if isinstance(expected, str) else split_type not in expected):
            raise ValueError(f"{self.experiment_mode} has an incompatible split type")
        selection_allowed = {"parameter_grid", "parameter_constraints", "selection_objective",
                             "objective_direction", "tie_break_rules", "minimum_observations",
                             "failure_policy", "diagnostic_selection_sensitivity", "metric_constraints"}
        unknown = set(self.parameter_selection) - selection_allowed
        required = {"parameter_grid", "parameter_constraints", "selection_objective",
                    "objective_direction", "tie_break_rules", "minimum_observations", "failure_policy"}
        if unknown or required - set(self.parameter_selection):
            raise ValueError(f"parameter_selection fields invalid; unknown={sorted(unknown)}, missing={sorted(required-set(self.parameter_selection))}")
        grid = self.parameter_selection["parameter_grid"]
        if not isinstance(grid, Mapping) or not grid or any(not isinstance(v, list) or not v for v in grid.values()):
            raise ValueError("parameter_grid must be a nonempty object of nonempty arrays")
        if self.parameter_selection["objective_direction"] not in {"maximize", "minimize"}:
            raise ValueError("objective_direction must be maximize or minimize")
        if self.parameter_selection["failure_policy"] not in {"fail_fast", "continue"}:
            raise ValueError("failure_policy must be fail_fast or continue")
        objective = self.parameter_selection["selection_objective"]
        ties = self.parameter_selection["tie_break_rules"]
        if not isinstance(objective, str) or "test" in objective.lower() or not isinstance(ties, list):
            raise ValueError("selection rules must be explicit and cannot reference test metrics")
        if any(not isinstance(x, str) or "test" in x.lower() for x in ties):
            raise ValueError("tie-break rules cannot reference test metrics")
        for rule in ties:
            if rule.startswith(("metric:", "metric_desc:")):
                metric = rule.split(":", 1)[1]
                if not metric or metric not in SUPPORTED_VALIDATION_METRICS:
                    raise ValueError(f"unsupported validation tie-break metric: {metric!r}")
        if not isinstance(self.parameter_selection["parameter_constraints"], list):
            raise ValueError("parameter_constraints must be an array")
        metric_constraints = self.parameter_selection.get("metric_constraints", [])
        if not isinstance(metric_constraints, list):
            raise ValueError("metric_constraints must be an array")
        for rule in metric_constraints:
            if (not isinstance(rule, Mapping) or set(rule) != {"metric", "operator", "value"}
                    or rule["operator"] not in {"lt", "le", "gt", "ge"}
                    or not isinstance(rule["metric"], str)
                    or not isinstance(rule["value"], (int, float))):
                raise ValueError("metric constraints require metric, operator, and numeric value")
        limits_allowed = {"maximum_candidate_count", "maximum_folds", "wall_clock_seconds", "execution"}
        if set(self.resource_limits) - limits_allowed or "maximum_candidate_count" not in self.resource_limits:
            raise ValueError("resource_limits requires maximum_candidate_count and contains an unknown field")
        if self.resource_limits.get("execution", "serial") != "serial":
            raise ValueError("only deterministic serial execution is supported")
        count = 1
        for values in grid.values(): count *= len(values)
        maximum = self.resource_limits["maximum_candidate_count"]
        if not isinstance(maximum, int) or maximum < 1 or count > maximum:
            raise ValueError("parameter grid exceeds maximum_candidate_count")
        if self.experiment_mode == "walk_forward" and (not isinstance(self.resource_limits.get("maximum_folds"), int)
                                                        or self.resource_limits["maximum_folds"] < 1):
            raise ValueError("walk_forward requires a positive maximum_folds")
        split_allowed = ({"type", "train_start", "train_end", "validation_start", "validation_end",
                          "test_start", "test_end", "warmup_observations"} if split_type == "fixed" else
                         {"type", "train_observations", "validation_observations", "test_observations",
                          "step_observations", "warmup_observations"})
        if set(self.split_definitions) != split_allowed:
            raise ValueError(f"split_definitions for {split_type} requires exactly {sorted(split_allowed)}")
        cost_allowed = {"closed_loop", "fixed_path"}
        if set(self.cost_analysis) - cost_allowed:
            raise ValueError("cost_analysis contains unknown fields")
        for kind, scenarios in self.cost_analysis.items():
            if not isinstance(scenarios, Mapping):
                raise ValueError(f"cost_analysis.{kind} must be an object of named scenarios")
            for name, costs in scenarios.items():
                if not isinstance(name, str) or not name or not isinstance(costs, Mapping):
                    raise ValueError("cost scenarios require nonempty names and cost objects")
                if set(costs) - {"commission_rate", "minimum_commission", "slippage_rate"}:
                    raise ValueError("cost scenario contains an unsupported cost field")
                if any(not isinstance(value, (int, float)) or value < 0 for value in costs.values()):
                    raise ValueError("cost scenario values must be nonnegative numbers")

    @property
    def experiment_id(self) -> str:
        raise RuntimeError("a configuration alone has no experiment ID; call experiment_identity with a validated manifest")

    @property
    def configuration_fingerprint(self) -> str:
        return hashlib.sha256(_canonical(asdict(self)).encode()).hexdigest()

    def to_json(self, path: str | Path | None = None) -> str:
        text = json.dumps(asdict(self), sort_keys=True, indent=2, allow_nan=False)
        if path is not None:
            Path(path).write_text(text + "\n", encoding="utf-8")
        return text

    @classmethod
    def from_json(cls, source: str | Path) -> "ExperimentConfig":
        text = str(source)
        candidate = None if text.lstrip().startswith(("{", "[")) else Path(source)
        raw = candidate.read_text(encoding="utf-8") if candidate is not None else text
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("experiment configuration must be an object")
        fields = set(cls.__dataclass_fields__)
        unknown = set(data) - fields
        required = {"schema_version", "experiment_name", "universe", "start_date", "end_date",
                    "strategy_class", "portfolio_constructor", "initial_cash", "fee_parameters",
                    "slippage_parameters", "execution_parameters", "constraint_parameters",
                    "benchmark_definitions", "output_directory", "split_definitions", "data_manifest", "data_root",
                    "experiment_mode", "warmup_policy"}
        missing = required - set(data)
        if unknown or missing:
            raise ValueError(f"configuration fields invalid; unknown={sorted(unknown)}, missing={sorted(missing)}")
        data["universe"] = tuple(data["universe"])
        return cls(**data)


@dataclass(frozen=True)
class Fold:
    train: tuple[date, ...]
    validation: tuple[date, ...]
    test: tuple[date, ...]
    warmup: tuple[date, ...] = ()

    def __post_init__(self):
        sets = [set(self.train), set(self.validation), set(self.test)]
        if any(sets[i] & sets[j] for i in range(3) for j in range(i + 1, 3)):
            raise ValueError("train, validation and test overlap")
        if self.train and self.validation and max(self.train) >= min(self.validation):
            raise ValueError("validation must follow train")
        if self.validation and self.test and max(self.validation) >= min(self.test):
            raise ValueError("test must follow validation")


@dataclass(frozen=True)
class SelectionFold:
    """The only fold information visible while selecting parameters.

    Deliberately has no test dates, making test-period inspection through the
    selection callback impossible rather than merely discouraged.
    """

    train: tuple[date, ...]
    validation: tuple[date, ...]
    warmup: tuple[date, ...] = ()


def _days(dates: Sequence[date]) -> tuple[date, ...]:
    values = tuple(sorted(set(dates)))
    if not values:
        raise ValueError("trading calendar is empty")
    return values


@dataclass(frozen=True)
class FixedSplit:
    train_end: date
    validation_end: date
    warmup_days: int = 0

    def split(self, dates: Sequence[date]) -> tuple[Fold, ...]:
        d = _days(dates)
        train = tuple(x for x in d if x <= self.train_end)
        valid = tuple(x for x in d if self.train_end < x <= self.validation_end)
        test = tuple(x for x in d if x > self.validation_end)
        start = d.index(test[0]) if test else len(d)
        return (Fold(train, valid, test, d[max(0, start - self.warmup_days) : start]),)


@dataclass(frozen=True)
class RollingWalkForward:
    train_days: int
    validation_days: int
    test_days: int
    step_days: int | None = None
    warmup_days: int = 0

    def split(self, dates: Sequence[date]) -> tuple[Fold, ...]:
        d, out = _days(dates), []
        size = self.train_days + self.validation_days + self.test_days
        step = self.step_days or self.test_days
        for start in range(0, len(d) - size + 1, step):
            a, b = (
                start + self.train_days,
                start + self.train_days + self.validation_days,
            )
            out.append(
                Fold(
                    d[start:a],
                    d[a:b],
                    d[b : b + self.test_days],
                    d[max(0, b - self.warmup_days) : b],
                )
            )
        return tuple(out)


@dataclass(frozen=True)
class ExpandingWalkForward(RollingWalkForward):
    def split(self, dates: Sequence[date]) -> tuple[Fold, ...]:
        d, out = _days(dates), []
        step = self.step_days or self.test_days
        b = self.train_days
        while b + self.validation_days + self.test_days <= len(d):
            c = b + self.validation_days
            out.append(
                Fold(
                    d[:b],
                    d[b:c],
                    d[c : c + self.test_days],
                    d[max(0, c - self.warmup_days) : c],
                )
            )
            b += step
        return tuple(out)


class FittableStrategy(Protocol):
    def fit(self, train_view: Any) -> None: ...
    def get_fitted_state(self) -> Mapping[str, Any]: ...
    def load_fitted_state(self, state: Mapping[str, Any]) -> None: ...


@dataclass(frozen=True)
class SearchResult:
    parameters: Mapping[str, Any]
    metrics: Mapping[str, float]
    error: str | None = None
    cached: bool = False


class GridSearch:
    """Deterministic grid search. ``evaluate`` must use train/validation only."""

    def __init__(
        self,
        grid: Mapping[str, Sequence[Any]],
        objective="sharpe",
        constraints=None,
        validator: Callable[[Mapping[str, Any]], None] | None = None,
        parallelism=1,
    ):
        if parallelism < 1:
            raise ValueError("parallelism must be positive")
        self.grid, self.objective = grid, objective
        self.constraints, self.validator = constraints or {}, validator

    def combinations(self):
        keys = sorted(self.grid)
        return tuple(
            dict(zip(keys, values))
            for values in itertools.product(*(self.grid[k] for k in keys))
        )

    def run(self, evaluate: Callable[[Mapping[str, Any]], Mapping[str, float]]):
        cache, results = {}, []
        for params in self.combinations():
            key = _canonical(params)
            if key in cache:
                results.append(replace(cache[key], cached=True))
                continue
            try:
                if self.validator:
                    self.validator(params)
                result = SearchResult(params, dict(evaluate(params)))
            except Exception as exc:  # one bad candidate must not abort the study
                result = SearchResult(params, {}, f"{type(exc).__name__}: {exc}")
            cache[key] = result
            results.append(result)
        return tuple(results)

    def select(self, results: Sequence[SearchResult]) -> SearchResult:
        eligible = []
        for result in results:
            if result.error:
                continue
            if all(
                self._constraint(result.metrics.get(k), rule)
                for k, rule in self.constraints.items()
            ):
                eligible.append(result)
        if not eligible:
            raise ValueError("no eligible parameter combination")
        return min(
            eligible,
            key=lambda r: (
                -float(r.metrics.get(self.objective, float("-inf"))),
                _canonical(r.parameters),
            ),
        )

    @staticmethod
    def _constraint(value, rule):
        if value is None:
            return False
        op, threshold = rule if isinstance(rule, (tuple, list)) else ("<=", rule)
        return value <= threshold if op == "<=" else value >= threshold


def walk_forward_select(
    folds: Sequence[Fold],
    search: GridSearch,
    evaluate_validation: Callable[[SelectionFold, Mapping[str, Any]], Mapping[str, float]],
    evaluate_test: Callable[[Fold, Mapping[str, Any]], Mapping[str, float]],
) -> tuple[dict[str, Any], ...]:
    """Select on validation and call test exactly once with frozen parameters.

    Separate callbacks make accidental use of test results during selection
    structurally impossible. Fitting, when required, belongs in the validation
    callback and its serialized state should be loaded by the test callback.
    """
    output = []
    oos_dates: set[date] = set()
    for number, fold in enumerate(folds):
        if tuple(sorted(fold.test)) != fold.test or oos_dates.intersection(fold.test):
            raise ValueError("out-of-sample dates must be unique and ordered")
        oos_dates.update(fold.test)
        selection_view = SelectionFold(fold.train, fold.validation, fold.warmup)
        candidates = search.run(lambda p: evaluate_validation(selection_view, p))
        selected = search.select(candidates)
        test_metrics = dict(evaluate_test(fold, selected.parameters))
        output.append(
            {
                "fold": number,
                "boundaries": {
                    "train": [str(x) for x in fold.train],
                    "validation": [str(x) for x in fold.validation],
                    "test": [str(x) for x in fold.test],
                    "warmup": [str(x) for x in fold.warmup],
                },
                "selected_parameters": dict(selected.parameters),
                "validation_metrics": dict(selected.metrics),
                "test_metrics": test_metrics,
                "selection_table": [
                    {"parameters": dict(candidate.parameters), "metrics": dict(candidate.metrics),
                     "error": candidate.error, "cached": candidate.cached}
                    for candidate in candidates
                ],
            }
        )
    return tuple(output)


def experiment_metadata(config: ExperimentConfig, manifest_path="", symbols=(), actual_dates=(),
                        *, git_sha=None, dependency_versions=None) -> dict:
    def version(name):
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            return None

    try:
        sha = git_sha or subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("Git commit provenance is required for an experiment") from exc
    if len(sha) != 40:
        raise ValueError("Git commit provenance must be a full 40-character SHA")
    manifest = Path(manifest_path) if manifest_path else None
    source_digest = hashlib.sha256()
    package_root = Path(__file__).parent
    for source in sorted(package_root.rglob("*.py")):
        source_digest.update(str(source.relative_to(package_root)).encode())
        source_digest.update(source.read_bytes())
    try:
        declared_version = tomllib.loads((package_root.parents[1] / "pyproject.toml").read_text())["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError("package version provenance is unavailable") from exc
    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit_sha": sha,
        "python_version": platform.python_version(),
        "kelaode_version": version("kelaode") or declared_version,
        "source_tree_sha256": source_digest.hexdigest(),
        "dependency_versions": dependency_versions or {
            x: version(x) for x in ("numpy", "pandas", "matplotlib")
        },
        "data_manifest_path": str(manifest or ""),
        "data_manifest_hash": hashlib.sha256(manifest.read_bytes()).hexdigest()
        if manifest and manifest.exists()
        else None,
        "symbols": list(symbols or config.universe),
        "actual_start_date": str(min(actual_dates)) if actual_dates else None,
        "actual_end_date": str(max(actual_dates)) if actual_dates else None,
        "strategy_parameters": dict(config.strategy_parameters),
        "portfolio_constructor_parameters": dict(config.constructor_parameters),
        "execution_parameters": dict(config.execution_parameters),
        "fee_parameters": dict(config.fee_parameters),
        "slippage_parameters": dict(config.slippage_parameters),
        "random_seed": config.random_seed,
    }

def experiment_identity(config: ExperimentConfig, manifest: SnapshotManifest,
                        provenance: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return the complete canonical identity; timestamps are intentionally excluded."""
    meta = dict(provenance or experiment_metadata(config, config.data_manifest))
    meta.pop("created_at_utc", None)
    payload = {
        "experiment_schema_version": EXPERIMENT_SCHEMA_VERSION,
        "configuration": asdict(config),
        "ordered_universe": list(config.universe),
        "strategy": {"class": config.strategy_class, "parameters": dict(config.strategy_parameters)},
        "portfolio_constructor": {"class": config.portfolio_constructor, "parameters": dict(config.constructor_parameters)},
        "constraints": dict(config.constraint_parameters), "initial_cash": config.initial_cash,
        "fees": dict(config.fee_parameters), "slippage": dict(config.slippage_parameters),
        "execution": dict(config.execution_parameters), "benchmarks": dict(config.benchmark_definitions),
        "splits": dict(config.split_definitions), "manifest_hash": manifest.hash,
        "input_hashes": [x.sha256 for x in manifest.entries], "provenance": meta,
    }
    digest = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
    return {"experiment_id": digest, "canonical_inputs": payload}


REQUIRED_OUTPUTS = ("artifact_manifest.json benchmark_cash.csv benchmark_curve.csv benchmark_fills.csv "
 "benchmark_marks.csv benchmark_metrics.json benchmark_orders.csv benchmark_positions.csv benchmark_weights.csv cash.csv "
 "configuration.json contracts.json daily_audits.json data_manifest.json drawdown.csv equity_curve.csv "
 "exposure.csv fills.csv fold_results.json generated_orders.csv identity.json marks.csv metrics.json "
 "orders.csv parameter_results.csv positions.csv rejections.csv runtime.json selected_parameters.json "
 "resolved_benchmark.json split_definitions.json trades.csv turnover.csv validated_orders.csv weights.csv").split()


def initialize_output(config: ExperimentConfig, metadata_value: Mapping[str, Any] | None = None,
                      *, identity: Mapping[str, Any] | None = None) -> Path:
    """Unsafe legacy placeholder creation was removed in schema 2.0."""
    raise RuntimeError("initialize_output cannot create auditable results; use runner.run_experiment")
