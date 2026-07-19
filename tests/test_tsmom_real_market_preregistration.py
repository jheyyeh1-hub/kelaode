from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from kelaode.experiment import ExperimentConfig
from kelaode.time_series_trend import TimeSeriesTrendParameters
from kelaode.validation_audit import default_validation_policy
from kelaode.validation_judgment import evaluate_tsmom_judgment

FIXED = Path("configs/validation/tsmom_real_market_fixed.json")
WALK = Path("configs/validation/tsmom_real_market_walk_forward.json")
POLICY = Path("configs/validation/tsmom_validation_policy.json")


def candidates(config):
    grid = config.parameter_selection["parameter_grid"]
    return [dict(zip(grid, values)) for values in itertools.product(*grid.values())]


def test_protocol_configs_strictly_load_with_exact_feasible_grid_and_no_hidden_parameters():
    fixed, walk = ExperimentConfig.from_json(FIXED), ExperimentConfig.from_json(WALK)
    assert fixed.strategy_class == walk.strategy_class == "TimeSeriesTrendStrategy"
    assert fixed.split_definitions == {
        "type": "fixed", "train_start": "2019-06-12", "train_end": "2021-12-31",
        "validation_start": "2022-01-01", "validation_end": "2023-12-31",
        "test_start": "2024-01-01", "test_end": "2026-07-17", "warmup_observations": 253,
    }
    assert walk.split_definitions == {"type": "rolling", "train_observations": 504,
        "validation_observations": 252, "test_observations": 126,
        "step_observations": 126, "warmup_observations": 253}
    expected = {"trend_lookback", "volatility_lookback", "rebalance_frequency",
                "signal_buffer", "maximum_active_assets"}
    values = candidates(fixed)
    assert len(values) == len({json.dumps(x, sort_keys=True) for x in values}) == 72
    assert fixed.resource_limits["maximum_candidate_count"] == 72
    assert set(fixed.strategy_parameters) == set(fixed.parameter_selection["parameter_grid"]) == expected
    for value in values:
        TimeSeriesTrendParameters(**value)


def test_every_frozen_config_value_is_identity_affecting():
    config = ExperimentConfig.from_json(FIXED)
    baseline = config.configuration_fingerprint
    raw = json.loads(FIXED.read_text())
    # Representative mutation of every result-bearing top-level object is enough:
    # the fingerprint hashes the complete strict dataclass serialization.
    for field in ("universe", "start_date", "end_date", "strategy_parameters",
                  "constraint_parameters", "fee_parameters", "slippage_parameters",
                  "execution_parameters", "benchmark_definitions", "split_definitions",
                  "parameter_selection", "cost_analysis", "data_manifest", "data_root"):
        changed = json.loads(json.dumps(raw))
        value = changed[field]
        if isinstance(value, dict): value["__mutation__"] = 1
        elif isinstance(value, list): value.reverse()
        else: changed[field] = value + "-changed"
        try:
            candidate = ExperimentConfig.from_json(json.dumps(changed))
        except ValueError:
            continue
        assert candidate.configuration_fingerprint != baseline


def test_default_policy_and_snapshot_reuse_identity_are_committed():
    assert default_validation_policy("TimeSeriesTrendStrategy").resolve() == POLICY.resolve()
    policy = json.loads(POLICY.read_text())
    reuse = policy["snapshot_reuse"]
    manifest = Path(reuse["manifest"])
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == reuse["manifest_sha256"]
    entries = json.loads(manifest.read_text())["entries"]
    assert len(entries) == 9 and {x["symbol"] for x in entries} == set(policy["official_listing_dates"])
    assert {(x["provider"], x["endpoint"], x["adjustment"], x["requested_start"], x["requested_end"])
            for x in entries} == {("AKShare/Eastmoney", "fund_etf_hist_em", "qfq", "2005-01-01", "2026-07-17")}


def passing(**changes):
    metrics = {"evaluated": True, "audits_pass": True, "accounting_reconstructable": True,
        "post_result_protocol_change": False, "frozen_test_return": .01,
        "frozen_test_excess_return": .01, "stitched_oos_return": .01,
        "stitched_max_drawdown": -.35, "moderate_equity_ratio": .95,
        "severe_equity_ratio": .90, "max_positive_contribution_share": .60,
        "neighbor_sign_reversal": False, "parameter_instability": False}
    metrics.update(changes)
    return metrics


def test_tsmom_judgment_boundaries_precedence_and_incomplete_diagnostics():
    thresholds = json.loads(POLICY.read_text())["thresholds"]
    assert evaluate_tsmom_judgment(passing(), thresholds) == "PASS"
    assert evaluate_tsmom_judgment(passing(frozen_test_return=0), thresholds) == "CONDITIONAL"
    assert evaluate_tsmom_judgment(passing(diagnostic_status="INCOMPLETE",
        max_positive_contribution_share=None), thresholds) == "CONDITIONAL"
    assert evaluate_tsmom_judgment(passing(audits_pass=False,
        diagnostic_status="INCOMPLETE"), thresholds) == "FAIL"
    assert evaluate_tsmom_judgment(passing(accounting_reconstructable=False), thresholds) == "FAIL"
    assert evaluate_tsmom_judgment(passing(frozen_test_return=-.20), thresholds) == "FAIL"
    assert evaluate_tsmom_judgment(passing(severe_equity_ratio=.899), thresholds) == "CONDITIONAL"
    assert evaluate_tsmom_judgment(passing(parameter_instability=True), thresholds) == "CONDITIONAL"


def test_frozen_sit_files_are_byte_identical_to_merge_baseline():
    expected = {
      "configs/validation/sit_real_market_fixed.json":"0bc6c7974021ee62ad3abbdaef48c7d2236990210546a92b9b403e5738066aba",
      "configs/validation/sit_real_market_walk_forward.json":"297988f0a42335e1718113cdf36af85519041888ec8f9478fec93b4222f9a8a3",
      "configs/validation/sit_validation_policy.json":"a3ebc47943b38ef8d246a6691817e8fd58c6e72ecbf8140441b48590e54eb00e",
      "docs/validation/sit_real_market_protocol.md":"70b9e0c50dc7bae2699d1c083cf36bd06351c8f4c3e3b51b42a6dfd045c45b86",
      "docs/validation/sit_real_market_report.md":"691984b4bf6001106a660bf09dcd2b2891300d4c90d4f20da62b67b52d79864b",
      "docs/validation/sit_real_market_judgment_inputs.json":"47c240ab7af00f27940871a765573ee00075b6827b433d5761435f0419d1cfa8",
    }
    assert {name: hashlib.sha256(Path(name).read_bytes()).hexdigest()
            for name in expected} == expected
