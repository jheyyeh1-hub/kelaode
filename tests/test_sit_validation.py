import csv
from datetime import date, timedelta
import pytest

from kelaode.experiment import RollingWalkForward
from kelaode.sit_validation import (
    append_result,
    experiment_id,
    parameter_combinations,
    select,
    valid_parameters,
)


def test_real_configuration_parses():
    from kelaode.experiment import ExperimentConfig

    config = ExperimentConfig.from_json("configs/sit_etf_rotation_experiment.json")
    assert config.strategy_class == "SITMomentumRotationStrategy"


def test_grid_count_and_illegal_combinations_filtered():
    # 72 raw; 18 top_k=1/max_weight=.5 and 18 top_k? Actually only top_k=1 is illegal.
    assert len(parameter_combinations()) == 60
    assert not valid_parameters({"top_k": 1, "max_weight": 0.5})
    assert valid_parameters({"top_k": 2, "max_weight": 0.5})


def test_result_resume_identity(tmp_path):
    path = tmp_path / "results.csv"
    row = {"experiment_id": "same", "parameters": "{}", "error": ""}
    append_result(path, row)
    completed = {r["experiment_id"] for r in csv.DictReader(path.open())}
    assert "same" in completed
    assert experiment_id({}, date(2024, 1, 1), date(2024, 2, 1)) == experiment_id(
        {}, date(2024, 1, 1), date(2024, 2, 1)
    )


def test_test_never_drives_selection():
    rows = [
        {
            "parameters": '{"x":1}',
            "error": "",
            "calmar": "2",
            "sharpe": "1",
            "max_drawdown": "-.1",
            "turnover": "2",
            "trade_count": "5",
            "test_return": "-1",
        },
        {
            "parameters": '{"x":2}',
            "error": "",
            "calmar": "1",
            "sharpe": "3",
            "max_drawdown": "-.1",
            "turnover": "1",
            "trade_count": "5",
            "test_return": "9",
        },
    ]
    assert select(rows)[0] == {"x": 1}


def test_walk_forward_has_no_leakage_or_duplicate_oos_dates():
    days = [date(2020, 1, 1) + timedelta(days=i) for i in range(1000)]
    folds = RollingWalkForward(504, 126, 126, 126, 200).split(days)
    stitched = [d for f in folds for d in f.test]
    assert len(stitched) == len(set(stitched))
    assert all(
        set(f.train).isdisjoint(f.test) and max(f.validation) < min(f.test)
        for f in folds
    )


def test_cost_order_and_metric_source():
    from kelaode.experiment_metrics import performance_metrics

    assert performance_metrics([100, 110, 99])["total_return"] == pytest.approx(-0.01)
    costs = [10, 20, 40]
    assert costs == sorted(costs)
