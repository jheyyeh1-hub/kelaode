from __future__ import annotations

import json
from dataclasses import replace

import pytest

from kelaode.experiment import ExperimentConfig
from kelaode.selection_runner import run_fixed_selection
from kelaode.validation_audit import audit_selection


def test_real_configs_strict_load_and_freeze_protocol_grid():
    fixed = ExperimentConfig.from_json("configs/validation/sit_real_market_fixed.json")
    walk = ExperimentConfig.from_json("configs/validation/sit_real_market_walk_forward.json")
    assert fixed.experiment_mode == "fixed_selection"
    assert walk.experiment_mode == "walk_forward"
    assert len(fixed.universe) == 9
    count = 1
    for values in fixed.parameter_selection["parameter_grid"].values():
        count *= len(values)
    assert count == fixed.resource_limits["maximum_candidate_count"] == 16
    assert fixed.parameter_selection["metric_constraints"] == [
        {"metric": "max_drawdown", "operator": "ge", "value": -0.35}
    ]


def test_metric_constraint_loading_is_strict():
    raw = json.loads(open("configs/sit_synthetic_fixed.json", encoding="utf-8").read())
    raw["parameter_selection"]["metric_constraints"] = [{"metric": "max_drawdown"}]
    with pytest.raises(ValueError, match="metric constraints"):
        ExperimentConfig.from_json(json.dumps(raw))


def test_read_only_audit_reconstructs_accounting_and_detects_change(tmp_path):
    config = ExperimentConfig.from_json("configs/sit_synthetic_fixed.json")
    root = run_fixed_selection(replace(config, output_directory=str(tmp_path / "results")))
    report = audit_selection(root)
    assert report["status"] == "pass"
    assert "equity_equals_cash_plus_marked_positions" in report["checks"]
    result = json.loads((root / "result.json").read_text())
    equity = root / result["frozen_test_bundle"] / "equity_curve.csv"
    equity.write_bytes(equity.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="artifact|child"):
        audit_selection(root)
