import json
from dataclasses import replace
from pathlib import Path
import pytest

from kelaode.cost_analysis import ReplayFill, fixed_path_cost_replay
from kelaode.experiment import (ExperimentConfig, FixedSplit, GridSearch, experiment_identity,
                                experiment_metadata, walk_forward_select)
from kelaode.runner import run_experiment
from kelaode.snapshot import SnapshotManifest

ROOT = Path(__file__).parent / "fixtures" / "snapshot"

def config(tmp_path, **changes):
    raw = json.loads(Path("configs/synthetic_example.json").read_text())
    raw.update(output_directory=str(tmp_path), data_root=str(ROOT), data_manifest=str(ROOT/"manifest.json"))
    raw.update(changes)
    if "initial_cash" in changes and "benchmark_definitions" not in changes:
        raw["benchmark_definitions"]["capital"] = changes["initial_cash"]
    return ExperimentConfig.from_json(json.dumps(raw))

def test_one_byte_mutation_invalidates_before_strategy(tmp_path):
    copied = tmp_path/"data"; copied.mkdir()
    for source in ROOT.iterdir():
        (copied/source.name).write_bytes(source.read_bytes())
    (copied/"AAA.csv").write_bytes((copied/"AAA.csv").read_bytes()+b" ")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        SnapshotManifest.load(copied/"manifest.json").validate(copied, expected_symbols=("AAA","BBB"))

def test_identity_changes_for_every_result_affecting_input(tmp_path):
    base=config(tmp_path); manifest=SnapshotManifest.load(base.data_manifest)
    provenance=experiment_metadata(base, base.data_manifest, git_sha="a"*40, dependency_versions={"x":"1"})
    first=experiment_identity(base,manifest,provenance)["experiment_id"]
    variants=[replace(base,universe=("BBB","AAA")),
              replace(base,initial_cash=100001, benchmark_definitions={**base.benchmark_definitions,"capital":100001}),
              replace(base,fee_parameters={"commission_rate":.01}),
              replace(base,execution_parameters={"execution_timing":"next_open","lot_size":1})]
    assert all(experiment_identity(x,manifest,provenance)["experiment_id"] != first for x in variants)
    changed=dict(provenance,git_commit_sha="b"*40)
    assert experiment_identity(base,manifest,changed)["experiment_id"] != first
    changed_source=dict(provenance,source_tree_sha256="f"*64)
    assert experiment_identity(base,manifest,changed_source)["experiment_id"] != first

def test_mixed_adjustments_rejected_by_default(tmp_path):
    raw=json.loads((ROOT/"manifest.json").read_text()); raw["entries"][1]["adjustment"]="qfq"
    path=tmp_path/"manifest.json"; path.write_text(json.dumps(raw))
    with pytest.raises(ValueError,match="mixed adjustment"):
        SnapshotManifest.load(path).validate(ROOT)

def test_cli_configuration_changes_run_and_exact_repeat_reuses(tmp_path):
    first=run_experiment(config(tmp_path)); assert run_experiment(config(tmp_path)) == first
    second=run_experiment(config(tmp_path,initial_cash=200000))
    assert first != second
    assert list(first.glob("*.png")) == []

def test_result_artifacts_reconstruct_final_accounting(tmp_path):
    root=run_experiment(config(tmp_path))
    import csv
    equities=list(csv.DictReader((root/"equity_curve.csv").open()))
    cash={r["date"]:float(r["cash"]) for r in csv.DictReader((root/"cash.csv").open())}
    positions=list(csv.DictReader((root/"positions.csv").open()))
    marks=list(csv.DictReader((root/"marks.csv").open()))
    for equity in equities:
        day=equity["date"]
        quantities={r["symbol"]:int(r["quantity"]) for r in positions if r["date"]==day}
        prices={r["symbol"]:float(r["close"]) for r in marks if r["date"]==day}
        assert float(equity["equity"]) == pytest.approx(cash[day]+sum(quantities[s]*prices[s] for s in prices))

def test_fixed_path_cost_replay_is_monotone():
    path=[ReplayFill("A","buy",10,100),ReplayFill("A","sell",10,110)]
    low=fixed_path_cost_replay(10000,path,commission_rate=.001,slippage_rate=.001)
    high=fixed_path_cost_replay(10000,path,commission_rate=.01,slippage_rate=.01)
    assert high <= low
    with pytest.raises(ValueError,match="final prices"):
        fixed_path_cost_replay(10000,[ReplayFill("A","buy",1,100)],commission_rate=0,slippage_rate=0)

def test_unknown_config_field_fails():
    raw=json.loads(Path("configs/synthetic_example.json").read_text()); raw["typo"]=1
    with pytest.raises(ValueError,match="unknown"):
        ExperimentConfig.from_json(json.dumps(raw))

def test_mutated_cached_artifact_is_never_reused(tmp_path):
    root=run_experiment(config(tmp_path)); (root/"cash.csv").write_bytes((root/"cash.csv").read_bytes()+b"x")
    with pytest.raises(ValueError,match="artifact failed integrity"):
        run_experiment(config(tmp_path))

def test_ignored_execution_modes_fail_loudly(tmp_path):
    with pytest.raises(ValueError,match="portfolio_constructor"):
        replace(config(tmp_path),portfolio_constructor="ignored-constructor")
    with pytest.raises(ValueError,match="no-fit"):
        run_experiment(config(tmp_path,split_definitions={"type":"fixed","train_end":"2024-01-02","validation_end":"2024-01-03"}))

def test_benchmark_uses_exact_strategy_calendar_capital_costs_and_timing(tmp_path):
    definitions={"symbols":["AAA"],"capital":100000,"execution_timing":"next_open"}
    root=run_experiment(config(tmp_path,benchmark_definitions=definitions))
    import csv
    strategy=list(csv.DictReader((root/"equity_curve.csv").open()))
    benchmark=list(csv.DictReader((root/"benchmark_curve.csv").open()))
    assert [x["date"] for x in strategy] == [x["date"] for x in benchmark]
    assert float(strategy[0]["equity"]) == float(benchmark[0]["equity"]) == 100000
    assert json.loads((root/"identity.json").read_text())["canonical_inputs"]["fees"] == config(tmp_path).fee_parameters

def test_failed_run_does_not_publish_partial_bundle(tmp_path):
    bad=config(tmp_path,strategy_class="missing")
    with pytest.raises(ValueError,match="unregistered"):
        run_experiment(bad)
    assert list(tmp_path.iterdir()) == []

def test_cost_fields_cannot_be_silently_overridden_between_categories(tmp_path):
    with pytest.raises(ValueError,match="miscategorized"):
        config(tmp_path,fee_parameters={"commission_rate":.001,"slippage_rate":.5})

def test_walk_forward_persists_every_candidate_without_test_selection(tmp_path):
    from datetime import date, timedelta
    days=tuple(date(2024,1,1)+timedelta(i) for i in range(6))
    fold=FixedSplit(days[1],days[3]).split(days)[0]
    seen=[]
    output=walk_forward_select([fold],GridSearch({"p":[1,2]},objective="score"),
        lambda _fold,p:{"score":p["p"]},
        lambda _fold,p: seen.append(p.copy()) or {"score":-999 if p["p"]==2 else 999})
    assert output[0]["selected_parameters"] == {"p":2}
    assert [x["parameters"] for x in output[0]["selection_table"]] == [{"p":1},{"p":2}]
    assert seen == [{"p":2}]
