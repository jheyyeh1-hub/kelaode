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
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence


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

    def __post_init__(self):
        if self.initial_cash <= 0 or self.start_date > self.end_date:
            raise ValueError("invalid cash or date range")
        # Fail early rather than producing an experiment that cannot be restored.
        _canonical(asdict(self))

    @property
    def experiment_id(self) -> str:
        return hashlib.sha256(_canonical(asdict(self)).encode()).hexdigest()[:16]

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
    evaluate_validation: Callable[[Fold, Mapping[str, Any]], Mapping[str, float]],
    evaluate_test: Callable[[Fold, Mapping[str, Any]], Mapping[str, float]],
) -> tuple[dict[str, Any], ...]:
    """Select on validation and call test exactly once with frozen parameters.

    Separate callbacks make accidental use of test results during selection
    structurally impossible. Fitting, when required, belongs in the validation
    callback and its serialized state should be loaded by the test callback.
    """
    output = []
    for number, fold in enumerate(folds):
        candidates = search.run(lambda p: evaluate_validation(fold, p))
        selected = search.select(candidates)
        test_metrics = dict(evaluate_test(fold, selected.parameters))
        output.append(
            {
                "fold": number,
                "selected_parameters": dict(selected.parameters),
                "validation_metrics": dict(selected.metrics),
                "test_metrics": test_metrics,
            }
        )
    return tuple(output)


def experiment_metadata(
    config: ExperimentConfig, manifest_path="", symbols=(), actual_dates=()
) -> dict:
    def version(name):
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            return None

    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        sha = "unknown"
    manifest = Path(manifest_path) if manifest_path else None
    return {
        "experiment_id": config.experiment_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit_sha": sha,
        "python_version": platform.python_version(),
        "kelaode_version": version("kelaode") or "0.1.0",
        "dependency_versions": {
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
        "random_seed": config.random_seed,
    }


REQUIRED_OUTPUTS = (
    "metrics.json benchmark_metrics.json equity_curve.csv benchmark_curve.csv drawdown.csv trades.csv orders.csv fills.csv rejections.csv daily_audits.csv positions.csv weights.csv monthly_returns.csv yearly_returns.csv parameter_results.csv fold_results.csv selected_parameters.json report.md equity_curve.png drawdown.png monthly_returns.png"
).split()


def initialize_output(
    config: ExperimentConfig, metadata_value: Mapping[str, Any] | None = None
) -> Path:
    """Create an atomic experiment namespace and its complete output contract."""
    random.seed(config.random_seed)
    root = Path(config.output_directory) / config.experiment_id
    root.mkdir(parents=True, exist_ok=True)
    config.to_json(root / "config.json")
    (root / "metadata.json").write_text(
        json.dumps(metadata_value or experiment_metadata(config), indent=2) + "\n"
    )
    for name in REQUIRED_OUTPUTS:
        path = root / name
        if name.endswith(".json"):
            path.write_text("{}\n")
        elif name.endswith(".md"):
            path.write_text(f"# {config.experiment_name}\n")
        elif name.endswith(".csv"):
            path.write_text("")
        else:
            try:
                import matplotlib.pyplot as plt

                plt.figure()
                plt.title(name[:-4].replace("_", " "))
                plt.savefig(path)
                plt.close()
            except ImportError:
                path.touch()
    return root
