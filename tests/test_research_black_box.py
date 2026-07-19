"""Black-box checks with expectations derived independently of implementation helpers."""
from __future__ import annotations

import csv
import hashlib
import json
import os
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
    if ("initial_cash" in overrides and "benchmark_definitions" not in overrides and
            raw["benchmark_definitions"]["type"] != "none"):
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
    path = tmp_path / "authority.json"; path.write_text(json.dumps(config))
    completed = subprocess.run([sys.executable, "-m", "kelaode.experiment_cli", "run",
                                "--config", str(path)], cwd=REPO, text=True,
                               capture_output=True, check=True,
                               env={**os.environ, "PYTHONPATH": str(REPO / "src")})
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
    definition = {"type": "single_symbol_buy_and_hold", "symbol": "AAA", "capital": 100_000, "execution_timing": "next_open"}
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


@pytest.mark.parametrize("fills,kwargs,match", [
    ((ReplayFill("A", "sell", 1, 10),), {}, "oversells"),
    ((ReplayFill("", "buy", 1, 10),), {"final_prices": {"": 10}}, "invalid frozen"),
    ((ReplayFill("A", "buy", 1, float("nan")),), {"final_prices": {"A": 10}}, "invalid frozen"),
    ((ReplayFill("A", "buy", 1, 10),), {"final_prices": {"A": float("inf")}}, "finite"),
])
def test_fixed_path_replay_rejects_invalid_long_only_paths(fills, kwargs, match):
    with pytest.raises(ValueError, match=match):
        fixed_path_cost_replay(100, fills, commission_rate=0, slippage_rate=0, **kwargs)


def test_fixed_path_replay_rejects_nonfinite_costs_negative_cash_and_missing_marks():
    fill = (ReplayFill("A", "buy", 1, 10),)
    for cash, commission, slippage in ((-1, 0, 0), (100, float("nan"), 0), (100, 0, float("inf"))):
        with pytest.raises(ValueError):
            fixed_path_cost_replay(cash, fill, commission_rate=commission,
                                   slippage_rate=slippage, final_prices={"A": 10})
    with pytest.raises(ValueError, match="final prices"):
        fixed_path_cost_replay(100, fill, commission_rate=0, slippage_rate=0)


def test_bundle_contract_is_complete_and_every_declared_hash_is_independent(tmp_path):
    bundle = run_experiment(resolved_config(tmp_path))
    required = {"configuration.json", "identity.json", "data_manifest.json", "metrics.json",
        "benchmark_metrics.json", "equity_curve.csv", "cash.csv", "positions.csv", "marks.csv",
        "weights.csv", "generated_orders.csv", "orders.csv", "validated_orders.csv", "rejections.csv",
        "fills.csv", "trades.csv", "exposure.csv", "turnover.csv", "drawdown.csv",
        "benchmark_curve.csv", "split_definitions.json", "selected_parameters.json",
        "parameter_results.csv", "fold_results.json", "daily_audits.json", "runtime.json", "contracts.json",
        "resolved_benchmark.json"}
    manifest = json.loads((bundle / "artifact_manifest.json").read_text())
    assert set(manifest["artifacts"]) == required
    assert {p.name for p in bundle.iterdir()} == required | {"artifact_manifest.json"}
    for name, digest in manifest["artifacts"].items():
        assert hashlib.sha256((bundle / name).read_bytes()).hexdigest() == digest
    assert all((bundle / name).stat().st_size > 0 for name in required)


def test_union_calendar_preserves_missing_prelisting_marks_and_exact_accounting(tmp_path):
    snapshot = tmp_path / "staggered"; snapshot.mkdir()
    (snapshot / "AAA.csv").write_bytes((FIXTURE / "AAA.csv").read_bytes())
    bbb_lines = (FIXTURE / "BBB.csv").read_text().splitlines()
    (snapshot / "BBB.csv").write_text("\n".join([bbb_lines[0], *bbb_lines[3:]]) + "\n")
    source_manifest = json.loads((FIXTURE / "manifest.json").read_text())
    bbb = source_manifest["entries"][1]
    bbb.update(row_count=2, actual_start="2024-01-04",
               sha256=hashlib.sha256((snapshot / "BBB.csv").read_bytes()).hexdigest())
    (snapshot / "manifest.json").write_text(json.dumps(source_manifest))
    raw = json.loads((REPO / "configs" / "synthetic_example.json").read_text())
    raw.update(data_alignment_mode="union", data_root=str(snapshot),
               data_manifest=str(snapshot / "manifest.json"), output_directory=str(tmp_path / "results"))
    bundle = run_experiment(ExperimentConfig.from_json(json.dumps(raw)))
    prelisting = [row for row in rows(bundle / "marks.csv")
                  if row["symbol"] == "BBB" and row["date"] < "2024-01-04"]
    assert prelisting and all(row["available"] == "False" and row["close"] == "" for row in prelisting)
    positions = {(row["date"], row["symbol"]): int(row["quantity"])
                 for row in rows(bundle / "positions.csv")}
    assert all(positions[row["date"], "BBB"] == 0 for row in prelisting)
    cash = {row["date"]: float(row["cash"]) for row in rows(bundle / "cash.csv")}
    marks = {(row["date"], row["symbol"]): float(row["close"])
             for row in rows(bundle / "marks.csv") if row["available"] == "True"}
    for row in rows(bundle / "equity_curve.csv"):
        day = row["date"]
        expected = cash[day] + sum(positions[day, symbol] * price
            for (mark_day, symbol), price in marks.items() if mark_day == day)
        assert float(row["equity"]) == pytest.approx(expected)


def test_primary_metrics_reconcile_saved_equity_trades_and_turnover(tmp_path):
    bundle = run_experiment(resolved_config(tmp_path))
    metrics = json.loads((bundle / "metrics.json").read_text())
    trades = rows(bundle / "trades.csv")
    equity = rows(bundle / "equity_curve.csv")
    turnover = float(rows(bundle / "turnover.csv")[0]["turnover"])
    notional = sum(int(row["quantity"]) * float(row["price"]) for row in trades)
    commissions = sum(float(row["commission"]) for row in trades)
    assert metrics["total_return"] == pytest.approx(float(equity[-1]["equity"]) / float(equity[0]["equity"]) - 1)
    assert metrics["execution_count"] == metrics["trade_count"] == len(trades)
    assert metrics["traded_notional"] == pytest.approx(notional)
    assert metrics["total_commissions"] == pytest.approx(commissions)
    assert metrics["turnover"] == pytest.approx(turnover)
    assert metrics["realized_trade_count"] == 0
    assert metrics["win_rate"] is None and metrics["profit_factor"] is None


@pytest.mark.parametrize("command", ["grid-search", "walk-forward"])
def test_legacy_cli_commands_fail_with_schema2_migration_message(tmp_path, command):
    completed = subprocess.run([sys.executable, "-m", "kelaode.experiment_cli", command,
        "--config", str(REPO / "configs" / "synthetic_example.json")], cwd=REPO, text=True,
        capture_output=True, env={**os.environ, "PYTHONPATH": str(REPO / "src")})
    assert completed.returncode == 2
    assert f"{command} is unavailable for schema 2.0" in completed.stderr


def test_every_maintained_experiment_config_loads():
    maintained = sorted((REPO / "configs").glob("*.json"))
    assert {path.name for path in maintained} == {"example_momentum.json", "synthetic_example.json"}
    assert all(ExperimentConfig.from_json(path).schema_version == "2.0" for path in maintained)
