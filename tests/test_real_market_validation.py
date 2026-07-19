from __future__ import annotations

import json
from dataclasses import replace

import pytest

from kelaode.experiment import ExperimentConfig
from kelaode.selection_runner import run_fixed_selection
from kelaode.validation_audit import audit_selection
from kelaode.validation_judgment import evaluate_strategy_judgment


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
    scenarios = {"base": {"commission_rate": .001, "minimum_commission": 2, "slippage_rate": .001},
                 "moderate": {"commission_rate": .002, "minimum_commission": 3, "slippage_rate": .002}}
    config = replace(config, output_directory=str(tmp_path / "results"),
                     cost_analysis={"closed_loop": scenarios, "fixed_path": scenarios})
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"official_listing_dates": {
        "STABLE": "2024-01-24", "VOLATILE": "2024-01-24", "LATE": "2024-01-31"}}))
    root = run_fixed_selection(config)
    report = audit_selection(root, policy)
    assert report["status"] == "pass"
    assert "equity_equals_cash_plus_marked_positions" in report["checks"]
    result = json.loads((root / "result.json").read_text())
    equity = root / result["frozen_test_bundle"] / "equity_curve.csv"
    equity.write_bytes(equity.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="artifact|child"):
        audit_selection(root, policy)


@pytest.fixture
def thresholds():
    return json.loads(open("configs/validation/sit_validation_policy.json", encoding="utf-8").read())["thresholds"]


def _passing_metrics(**overrides):
    values = {"evaluated": True, "audits_pass": True, "frozen_test_return": 0.01,
              "frozen_test_excess_return": 0.01, "stitched_oos_return": 0.01,
              "stitched_max_drawdown": -0.35, "moderate_equity_ratio": 0.95,
              "severe_equity_ratio": 0.90, "max_positive_contribution_share": 0.60,
              "neighbor_sign_reversal": False, "post_result_protocol_change": False}
    values.update(overrides)
    return values


def test_judgment_not_evaluated_for_failed_freeze(thresholds):
    assert evaluate_strategy_judgment(None, thresholds) == "NOT_EVALUATED"
    assert evaluate_strategy_judgment({"evaluated": False}, thresholds) == "NOT_EVALUATED"


def test_judgment_pass_boundaries_and_strict_return_thresholds(thresholds):
    assert evaluate_strategy_judgment(_passing_metrics(), thresholds) == "PASS"
    assert evaluate_strategy_judgment(_passing_metrics(frozen_test_excess_return=0), thresholds) == "CONDITIONAL"
    assert evaluate_strategy_judgment(_passing_metrics(stitched_oos_return=0), thresholds) == "CONDITIONAL"
    assert evaluate_strategy_judgment(_passing_metrics(moderate_equity_ratio=.949999), thresholds) == "CONDITIONAL"


@pytest.mark.parametrize("change", [
    {"audits_pass": False}, {"frozen_test_return": -.20}, {"stitched_oos_return": -.20},
    {"stitched_max_drawdown": -.500001}, {"moderate_equity_ratio": .899999},
    {"post_result_protocol_change": True},
])
def test_judgment_fail_boundaries_and_precedence(thresholds, change):
    metrics = _passing_metrics(max_positive_contribution_share=.9, **change)
    assert evaluate_strategy_judgment(metrics, thresholds) == "FAIL"


@pytest.mark.parametrize("change", [
    {"max_positive_contribution_share": .600001}, {"severe_equity_ratio": .899999},
    {"neighbor_sign_reversal": True},
])
def test_judgment_conditional_triggers(thresholds, change):
    assert evaluate_strategy_judgment(_passing_metrics(**change), thresholds) == "CONDITIONAL"


def test_incomplete_diagnostics_are_provisionally_conditional_without_placeholders(thresholds):
    metrics = _passing_metrics()
    metrics.pop("max_positive_contribution_share")
    metrics.pop("neighbor_sign_reversal")
    metrics["diagnostic_status"] = "INCOMPLETE"
    assert evaluate_strategy_judgment(metrics, thresholds) == "CONDITIONAL"


def test_report_status_matches_mechanical_judgment(thresholds):
    report = open("docs/validation/sit_real_market_report.md", encoding="utf-8").read()
    persisted = json.loads(open(
        "docs/validation/sit_real_market_judgment_inputs.json", encoding="utf-8").read())
    assert "execution_status = COMPLETED" in report
    assert persisted["thresholds"] == thresholds
    expected = evaluate_strategy_judgment(persisted["metrics"], thresholds)
    assert persisted["strategy_judgment"] == expected
    assert f"strategy_judgment = {expected}" in report
