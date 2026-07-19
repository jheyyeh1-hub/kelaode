import json
import pytest
from datetime import date, timedelta
from kelaode.experiment import (
    ExperimentConfig,
    FixedSplit,
    RollingWalkForward,
    ExpandingWalkForward,
    GridSearch,
    experiment_metadata,
    initialize_output,
    REQUIRED_OUTPUTS,
)
from kelaode.experiment_metrics import (
    performance_metrics,
    benchmark_metrics,
    cost_scenarios,
)


def config(tmp_path):
    return ExperimentConfig(
        experiment_name="x", universe=("A", "B"), start_date="2020-01-01",
        end_date="2022-01-01", strategy_class="S", strategy_parameters={"b": 2, "a": 1},
        portfolio_constructor="strategy-native", constructor_parameters={}, initial_cash=100,
        fee_parameters={}, slippage_parameters={}, execution_parameters={"execution_timing": "next_open"},
        constraint_parameters={}, benchmark_definitions={
            "symbols": [], "capital": 100, "execution_timing": "next_open"},
        data_alignment_mode="intersection", random_seed=7, output_directory=str(tmp_path),
        split_definitions={"type": "none", "reason": "unit test"},
        data_manifest="manifest.json", data_root="data", notes="",
    )


def test_config_roundtrip_and_stable_id(tmp_path):
    c = config(tmp_path)
    raw = c.to_json()
    restored = ExperimentConfig.from_json(raw)
    assert restored == c and restored.configuration_fingerprint == c.configuration_fingerprint
    reordered = json.dumps(dict(reversed(list(json.loads(raw).items()))))
    assert ExperimentConfig.from_json(reordered).configuration_fingerprint == c.configuration_fingerprint


def test_splits_have_no_leakage(tmp_path):
    d = tuple(date(2020, 1, 1) + timedelta(i) for i in range(20))
    fixed = FixedSplit(d[7], d[11], 2).split(d)[0]
    assert fixed.warmup == d[10:12] and not (set(fixed.train) & set(fixed.test))
    rolling = RollingWalkForward(8, 4, 3, 3, 2).split(d)
    expanding = ExpandingWalkForward(8, 4, 3, 3, 2).split(d)
    assert len(rolling) == 2 and len(expanding) == 2
    assert len(rolling[1].train) == 8 and len(expanding[1].train) == 11


def test_grid_is_deterministic_ties_and_failures():
    search = GridSearch({"z": [2, 1], "a": [1]}, "sharpe", {"max_drawdown": 0.2})

    def evaluate(p):
        if p["z"] == 2:
            raise ValueError("bad")
        return {"sharpe": 1, "max_drawdown": -0.1}

    results = search.run(evaluate)
    assert results[0].error and search.select(results).parameters == {"a": 1, "z": 1}
    assert results == search.run(evaluate)


def test_metrics_edge_cases_and_alignment():
    assert performance_metrics([100])["trade_count"] == 0
    assert performance_metrics([100, 100])["sharpe"] == 0
    p = {1: 100, 2: 110, 3: 105}
    b = {2: 100, 3: 101, 4: 99}
    assert benchmark_metrics(p, b)["excess_return"] != 0
    assert set(cost_scenarios({"commission_rate": 1, "slippage_rate": 2})) == {
        "base",
        "double_commission",
        "double_slippage",
        "conservative_liquidity",
        "reduced_participation_rate",
        "delayed_execution",
    }


def test_metadata_and_output_contract(tmp_path):
    c = config(tmp_path)
    meta = experiment_metadata(c)
    assert len(meta["git_commit_sha"]) == 40
    with pytest.raises(RuntimeError, match="cannot create auditable results"):
        initialize_output(c, meta)

def test_configuration_alone_cannot_claim_complete_experiment_identity(tmp_path):
    with pytest.raises(RuntimeError, match="configuration alone"):
        _ = config(tmp_path).experiment_id
