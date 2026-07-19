"""Black-box coverage for SIT through the schema-2.0 execution path."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from kelaode.experiment import ExperimentConfig
from kelaode.market_data import read_daily_bars
from kelaode.open_source_rotation import SITMomentumRotationStrategy, SITRotationParameters
from kelaode.portfolio import MarketView
from kelaode.runner import run_experiment
from kelaode.snapshot import SnapshotManifest
from kelaode.strategy_registry import STRATEGY_REGISTRY, create_strategy

REPO = Path(__file__).parents[1]
CONFIG = REPO / "configs" / "sit_synthetic.json"
FIXTURE = REPO / "tests" / "fixtures" / "sit_snapshot"


def _config(tmp_path: Path, **updates) -> ExperimentConfig:
    raw = json.loads(CONFIG.read_text())
    raw.update(data_root=str(FIXTURE), data_manifest=str(FIXTURE / "manifest.json"),
               output_directory=str(tmp_path / "results"))
    raw.update(updates)
    return ExperimentConfig.from_json(json.dumps(raw))


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def test_config_loads_and_registry_parses_dataclass_parameters(tmp_path):
    config = _config(tmp_path)
    strategy = create_strategy(config.strategy_class, config.universe, config.strategy_parameters)
    assert config.schema_version == "2.0"
    assert "SITMomentumRotationStrategy" in STRATEGY_REGISTRY
    assert isinstance(strategy, SITMomentumRotationStrategy)
    assert isinstance(strategy.parameters, SITRotationParameters)
    assert strategy.parameters.momentum_lookback == 3


def test_shared_runner_emits_complete_aligned_bundle_and_reuses_it(tmp_path):
    config = _config(tmp_path)
    first = run_experiment(config)
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
              for path in first.iterdir() if path.is_file()}
    second = run_experiment(config)
    assert second == first
    assert hashes == {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in second.iterdir() if path.is_file()}
    contract = json.loads((first / "artifact_manifest.json").read_text())
    assert set(contract["artifacts"]) == {path.name for path in first.iterdir()
                                           if path.name != "artifact_manifest.json"}
    assert [row["date"] for row in _rows(first / "equity_curve.csv")] == [
        row["date"] for row in _rows(first / "benchmark_curve.csv")]
    trades = _rows(first / "trades.csv")
    assert trades and all(int(row["quantity"]) % 100 == 0 for row in trades)
    assert all(float(row["commission"]) >= 2 for row in trades)
    orders = _rows(first / "orders.csv")
    assert all(row["date"] > row["signal_date"] for row in orders if row["side"] != "none")


def test_independent_rank_listing_missing_bar_trend_volatility_and_hold_behavior():
    data = {symbol: read_daily_bars(FIXTURE / f"{symbol}.csv")
            for symbol in ("STABLE", "VOLATILE", "LATE")}
    parameters = SITRotationParameters(momentum_lookback=3, top_k=2, trend_window=3,
        volatility_lookback=3, rebalance_frequency="interval", rebalance_interval=3,
        minimum_listing_age=4, max_weight=.8)
    strategy = SITMomentumRotationStrategy(tuple(data), parameters)

    missing_day = date(2024, 1, 30)
    assert "VOLATILE" not in strategy.scores(4, missing_day, MarketView(data, missing_day), None)
    before_age = date(2024, 1, 31)
    assert "LATE" not in strategy.scores(5, before_age, MarketView(data, before_age), None)

    signal_day = date(2024, 2, 6)
    view = MarketView(data, signal_day)
    independently_ranked = []
    for symbol in data:
        closes = view.history(symbol, "close", 4)
        if (view.is_tradable(symbol) and view.listing_age(symbol) >= 4 and len(closes) == 4
                and closes[-1] > sum(view.history(symbol, "close", 3)) / 3):
            independently_ranked.append((closes[-1] / closes[0], symbol))
    independently_ranked.sort(key=lambda item: (-item[0], item[1]))
    target = strategy.target_weights(9, signal_day, view, None)
    assert set(target) == {symbol for _, symbol in independently_ranked[:2]}
    # Inverse volatility gives the smoother selected series more weight.
    assert target["LATE"] > target["VOLATILE"]
    # Index seven is not an interval boundary, so the engine must retain targets.
    from kelaode.portfolio import HoldTargets
    assert isinstance(strategy.target_weights(7, date(2024, 2, 2),
                      MarketView(data, date(2024, 2, 2)), None), HoldTargets)


def test_future_file_change_cannot_change_earlier_targets_or_orders(tmp_path):
    baseline = run_experiment(_config(tmp_path / "baseline"))
    copied = tmp_path / "future"; shutil.copytree(FIXTURE, copied)
    late = copied / "LATE.csv"
    lines = late.read_text().splitlines()
    fields = lines[-1].split(","); fields[4] = "62"; fields[2] = "62.31"
    lines[-1] = ",".join(fields); late.write_text("\n".join(lines) + "\n")
    manifest = json.loads((copied / "manifest.json").read_text())
    manifest["entries"][2]["sha256"] = hashlib.sha256(late.read_bytes()).hexdigest()
    (copied / "manifest.json").write_text(json.dumps(manifest))
    changed = _config(tmp_path / "changed", data_root=str(copied),
        data_manifest=str(copied / "manifest.json"))
    mutated = run_experiment(changed)
    cutoff = "2024-02-08"
    project = lambda bundle, name: [row for row in _rows(bundle / name)
                                    if row["date"] < cutoff]
    assert project(baseline, "weights.csv") == project(mutated, "weights.csv")
    assert project(baseline, "orders.csv") == project(mutated, "orders.csv")


def test_parameter_changes_identity_and_one_byte_mutation_is_rejected(tmp_path):
    config = _config(tmp_path)
    original = run_experiment(config)
    changed_parameters = dict(config.strategy_parameters); changed_parameters["top_k"] = 1
    changed = run_experiment(replace(config, strategy_parameters=changed_parameters))
    assert changed.name != original.name

    copied = tmp_path / "mutated"; shutil.copytree(FIXTURE, copied)
    path = copied / "STABLE.csv"
    content = bytearray(path.read_bytes()); content[-2] ^= 1; path.write_bytes(content)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        SnapshotManifest.load(copied / "manifest.json").validate(
            copied, expected_symbols=config.universe)
