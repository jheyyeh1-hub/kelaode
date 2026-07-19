import json
from dataclasses import replace
from pathlib import Path
import pytest

from kelaode.cost_analysis import ReplayFill, fixed_path_cost_replay
from kelaode.experiment import ExperimentConfig, experiment_identity, experiment_metadata
from kelaode.runner import run_experiment
from kelaode.snapshot import SnapshotManifest

ROOT = Path(__file__).parent / "fixtures" / "snapshot"

def config(tmp_path, **changes):
    raw = json.loads(Path("configs/synthetic_example.json").read_text())
    raw.update(output_directory=str(tmp_path), data_root=str(ROOT), data_manifest=str(ROOT/"manifest.json"))
    raw.update(changes)
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
    variants=[replace(base,universe=("BBB","AAA")), replace(base,initial_cash=100001),
              replace(base,fee_parameters={"commission_rate":.01}),
              replace(base,execution_parameters={"lot_size":1})]
    assert all(experiment_identity(x,manifest,provenance)["experiment_id"] != first for x in variants)
    changed=dict(provenance,git_commit_sha="b"*40)
    assert experiment_identity(base,manifest,changed)["experiment_id"] != first

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
    equity=list(csv.DictReader((root/"equity_curve.csv").open()))[-1]
    cash=list(csv.DictReader((root/"cash.csv").open()))[-1]
    positions=list(csv.DictReader((root/"positions.csv").open()))
    marks={"AAA":13.5,"BBB":18.0}
    quantities={r["symbol"]:int(r["quantity"]) for r in positions if r["date"]==equity["date"]}
    assert float(equity["equity"]) == pytest.approx(float(cash["cash"])+sum(quantities[s]*marks[s] for s in marks))

def test_fixed_path_cost_replay_is_monotone():
    path=[ReplayFill("A","buy",10,100),ReplayFill("A","sell",10,110)]
    low=fixed_path_cost_replay(10000,path,commission_rate=.001,slippage_rate=.001)
    high=fixed_path_cost_replay(10000,path,commission_rate=.01,slippage_rate=.01)
    assert high <= low

def test_unknown_config_field_fails():
    raw=json.loads(Path("configs/synthetic_example.json").read_text()); raw["typo"]=1
    with pytest.raises(ValueError,match="unknown"):
        ExperimentConfig.from_json(json.dumps(raw))
