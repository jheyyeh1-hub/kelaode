"""Black-box checks with expectations derived independently of implementation helpers."""
from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from decimal import Decimal
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from kelaode.cost_analysis import ReplayFill, fixed_path_cost_replay
from kelaode.experiment import (ExperimentConfig, FixedSplit, GridSearch,
                                experiment_identity, experiment_metadata,
                                walk_forward_select)
from kelaode.market_data import DailyBar
from kelaode.portfolio import (CrossSectionalMomentumStrategy,
                               PortfolioBacktestConfig, PortfolioBacktester)
from kelaode.runner import run_experiment
from kelaode.snapshot import SnapshotManifest

REPO = Path(__file__).parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "snapshot"


def resolved_config(tmp_path: Path, **overrides) -> ExperimentConfig:
    raw = json.loads((REPO / "configs" / "synthetic_example.json").read_text())
    raw.update(data_manifest=str(FIXTURE / "manifest.json"), data_root=str(FIXTURE),
               output_directory=str(tmp_path))
    raw.update(overrides)
    if "initial_cash" in overrides and "benchmark_definitions" not in overrides:
        raw["benchmark_definitions"]["capital"] = overrides["initial_cash"]
    return ExperimentConfig.from_json(json.dumps(raw))


def rows(path: Path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def test_identity_is_sha256_of_canonical_complete_payload(tmp_path):
    config = resolved_config(tmp_path)
    manifest = SnapshotManifest.load(config.data_manifest)
    provenance = experiment_metadata(config, config.data_manifest,
        git_sha="1" * 40, dependency_versions={"independent": "7"})
    result = experiment_identity(config, manifest, provenance)
    assert {"configuration", "ordered_universe", "strategy", "portfolio_constructor", "constraints",
            "initial_cash", "fees", "slippage", "execution", "benchmarks", "splits",
            "manifest_hash", "input_hashes", "provenance", "experiment_schema_version"} == set(result["canonical_inputs"])
    assert result["canonical_inputs"]["input_hashes"] == [entry.sha256 for entry in manifest.entries]
    independently_encoded = json.dumps(result["canonical_inputs"], sort_keys=True,
                                       separators=(",", ":"), allow_nan=False).encode()
    assert result["experiment_id"] == hashlib.sha256(independently_encoded).hexdigest()
    reordered = json.loads(json.dumps(result["canonical_inputs"]))
    reordered["configuration"] = dict(reversed(list(reordered["configuration"].items())))
    assert hashlib.sha256(json.dumps(reordered, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == result["experiment_id"]


def test_exact_file_bytes_are_the_data_authority(tmp_path):
    copied = tmp_path / "snapshot"; copied.mkdir()
    for source in FIXTURE.iterdir():
        (copied / source.name).write_bytes(source.read_bytes())
    original_hash = hashlib.sha256((copied / "AAA.csv").read_bytes()).hexdigest()
    content = bytearray((copied / "AAA.csv").read_bytes()); content[-2] ^= 1
    (copied / "AAA.csv").write_bytes(content)
    assert hashlib.sha256(content).hexdigest() != original_hash
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        SnapshotManifest.load(copied / "manifest.json").validate(copied, expected_symbols=("AAA", "BBB"))


def test_cli_uses_configured_capital_and_output_location(tmp_path):
    output = tmp_path / "chosen-output"
    config = json.loads((REPO / "configs" / "synthetic_example.json").read_text())
    config.update(initial_cash=123_456, output_directory=str(output),
                  data_manifest=str(FIXTURE / "manifest.json"), data_root=str(FIXTURE))
    config["benchmark_definitions"]["capital"] = 123_456
    path = tmp_path / "authority.json"; path.write_text(json.dumps(config))
    completed = subprocess.run([sys.executable, "-m", "kelaode.experiment_cli", "run",
                                "--config", str(path)], cwd=REPO, text=True,
                               capture_output=True, check=True)
    result = Path(completed.stdout.strip().split("run: ", 1)[1])
    assert result.parent.resolve() == output.resolve()
    assert float(rows(result / "equity_curve.csv")[0]["equity"]) == 123_456


def test_every_daily_equity_reconstructs_without_engine_helpers(tmp_path):
    bundle = run_experiment(resolved_config(tmp_path))
    cash = {x["date"]: float(x["cash"]) for x in rows(bundle / "cash.csv")}
    positions = {(x["date"], x["symbol"]): int(x["quantity"]) for x in rows(bundle / "positions.csv")}
    marks = {(x["date"], x["symbol"]): float(x["close"]) for x in rows(bundle / "marks.csv")}
    symbols = {symbol for _, symbol in positions}
    for observation in rows(bundle / "equity_curve.csv"):
        day = observation["date"]
        independently_valued = cash[day] + sum(positions[day, s] * marks[day, s] for s in symbols)
        assert float(observation["equity"]) == pytest.approx(independently_valued, abs=1e-9)


def test_future_bar_mutation_cannot_change_earlier_orders():
    days = [date(2024, 1, 1) + timedelta(i) for i in range(5)]
    def market(last_close):
        closes_a, closes_b = [10, 11, 12, 13, last_close], [10, 9, 8, 7, 6]
        return {symbol: [DailyBar(day, close, close, close, close, 100_000)
                for day, close in zip(days, closes)]
                for symbol, closes in (("A", closes_a), ("B", closes_b))}
    config = PortfolioBacktestConfig(initial_cash=100_000, lot_size=1,
        minimum_commission=0, commission_rate=0, slippage_rate=0)
    strategy = lambda: CrossSectionalMomentumStrategy(("A", "B"), lookback=1, top_k=1)
    base = PortfolioBacktester(config).run(market(14), strategy())
    mutated = PortfolioBacktester(config).run(market(1_400), strategy())
    cutoff = days[-1]
    project = lambda result: [(o.trade_date, o.signal_date, o.symbol, o.side,
                               o.requested_quantity, o.filled_quantity)
                              for o in result.orders if o.signal_date < cutoff]
    assert project(base) == project(mutated)


def test_selection_callback_has_no_test_period_access():
    days = tuple(date(2024, 1, 1) + timedelta(i) for i in range(6))
    fold = FixedSplit(days[1], days[3]).split(days)[0]
    observed = []
    def validation(view, parameters):
        assert not hasattr(view, "test")
        return {"score": parameters["choice"]}
    def test_only(full_fold, parameters):
        observed.append((full_fold.test, parameters["choice"]))
        return {"poisoned_test_score": -parameters["choice"]}
    result = walk_forward_select((fold,), GridSearch({"choice": (1, 2)}, objective="score"),
                                 validation, test_only)
    assert result[0]["selected_parameters"] == {"choice": 2}
    assert observed == [(fold.test, 2)]


def test_benchmark_has_independently_computed_first_fill_accounting(tmp_path):
    definition = {"symbols": ["AAA"], "capital": 100_000, "execution_timing": "next_open"}
    bundle = run_experiment(resolved_config(tmp_path, benchmark_definitions=definition))
    benchmark = rows(bundle / "benchmark_curve.csv")
    strategy_dates = [x["date"] for x in rows(bundle / "equity_curve.csv")]
    assert [x["date"] for x in benchmark] == strategy_dates
    # Day two: buy 9,500 at 10.5 plus 5 bps slippage and 25 bps commission.
    execution_price = 10.5 * 1.0005
    notional = 9_500 * execution_price
    commission = notional * 0.00025
    expected_cash = 100_000 - notional - commission
    expected_equity = expected_cash + 9_500 * 11.5
    assert float(benchmark[1]["equity"]) == pytest.approx(expected_equity, abs=1e-9)


def test_fixed_path_cost_replay_matches_independent_arithmetic_and_is_monotone():
    path = (ReplayFill("A", "buy", 10, 100), ReplayFill("A", "sell", 10, 110))
    low = fixed_path_cost_replay(10_000, path, commission_rate=.001, slippage_rate=.001)
    expected = (Decimal("10000") - 10 * Decimal("100.1") - 10 * Decimal("100.1") * Decimal(".001")
                + 10 * Decimal("109.89") - 10 * Decimal("109.89") * Decimal(".001"))
    assert Decimal(str(low)) == expected
    assert fixed_path_cost_replay(10_000, path, commission_rate=.01, slippage_rate=.01) < low


def test_bundle_contract_is_complete_and_every_declared_hash_is_independent(tmp_path):
    bundle = run_experiment(resolved_config(tmp_path))
    required = {"configuration.json", "identity.json", "data_manifest.json", "metrics.json",
        "benchmark_metrics.json", "equity_curve.csv", "cash.csv", "positions.csv", "marks.csv",
        "weights.csv", "generated_orders.csv", "orders.csv", "validated_orders.csv", "rejections.csv",
        "fills.csv", "trades.csv", "exposure.csv", "turnover.csv", "drawdown.csv",
        "benchmark_curve.csv", "split_definitions.json", "selected_parameters.json",
        "parameter_results.csv", "fold_results.json", "daily_audits.json", "runtime.json", "contracts.json"}
    manifest = json.loads((bundle / "artifact_manifest.json").read_text())
    assert set(manifest["artifacts"]) == required
    assert {p.name for p in bundle.iterdir()} == required | {"artifact_manifest.json"}
    for name, digest in manifest["artifacts"].items():
        assert hashlib.sha256((bundle / name).read_bytes()).hexdigest() == digest
    assert all((bundle / name).stat().st_size > 0 for name in required)
