"""Adversarial black-box coverage for schema-2.0 selection orchestration."""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from kelaode.experiment import ExperimentConfig, Fold, experiment_identity, experiment_metadata
from kelaode.market_data import DailyBar
from kelaode.portfolio import PortfolioBacktestConfig, PortfolioBacktester
from kelaode.selection_runner import (candidate_identity, run_fixed_selection, run_walk_forward,
    stitch_oos_equity, validate_artifact_directory, _folds)
from kelaode.snapshot import SnapshotManifest

REPO = Path(__file__).parents[1]
FIXTURE = REPO / "tests/fixtures/sit_snapshot"
FIXED = REPO / "configs/sit_synthetic_fixed.json"
WALK = REPO / "configs/sit_synthetic_walk_forward.json"


def config(tmp_path: Path, source=FIXED, **updates) -> ExperimentConfig:
    raw = json.loads(Path(source).read_text())
    raw.update(updates)
    raw["data_root"] = updates.get("data_root", str(FIXTURE))
    raw["data_manifest"] = updates.get("data_manifest", str(FIXTURE/"manifest.json"))
    raw["output_directory"] = str(tmp_path/"results")
    return ExperimentConfig.from_json(json.dumps(raw))


def rows(path: Path):
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def result(root: Path):
    return json.loads((root/"result.json").read_text())


def copy_snapshot(tmp_path: Path, mutate_date: str, multiplier: float) -> Path:
    root = tmp_path/"snapshot"; shutil.copytree(FIXTURE, root)
    manifest = json.loads((root/"manifest.json").read_text())
    target = root/manifest["entries"][0]["relative_path"]
    table = rows(target)
    for row in table:
        if row["date"] == mutate_date:
            for field in ("open", "high", "low", "close"):
                row[field] = str(float(row[field])*multiplier)
    with target.open("w", newline="", encoding="utf-8") as stream:
        writer=csv.DictWriter(stream,fieldnames=table[0]); writer.writeheader(); writer.writerows(table)
    manifest["entries"][0]["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    (root/"manifest.json").write_text(json.dumps(manifest,sort_keys=True,indent=2)+"\n")
    return root


def test_stitch_compounds_fold_returns_without_reset_and_reconstructs_metrics():
    stitched = stitch_oos_equity((
        ({"date":"2024-01-01","equity":100},{"date":"2024-01-02","equity":110}),
        ({"date":"2024-01-03","equity":100},{"date":"2024-01-04","equity":105})), 100)
    assert [x["equity"] for x in stitched] == pytest.approx([100,110,110,115.5])
    assert stitched[-1]["equity"]/stitched[0]["equity"]-1 == pytest.approx(.155)
    peak=0; drawdowns=[]
    for item in stitched:
        peak=max(peak,item["equity"]); drawdowns.append(item["equity"]/peak-1)
    assert min(drawdowns) == 0


def test_stitch_one_observation_and_invalid_paths():
    assert stitch_oos_equity((({"date":"2024-01-01","equity":7},),),100)[0]["equity"] == 100
    with pytest.raises(ValueError, match="positive"):
        stitch_oos_equity((({"date":"2024-01-01","equity":0},),),100)
    with pytest.raises(ValueError, match="ordered"):
        stitch_oos_equity((({"date":"2024-01-02","equity":1},{"date":"2024-01-01","equity":2}),),100)


def test_fixed_selection_uses_validation_only_when_test_bytes_change(tmp_path):
    base = run_fixed_selection(config(tmp_path/"base"))
    changed_root = copy_snapshot(tmp_path/"changed", "2024-02-08", 1.7)
    changed = config(tmp_path/"changed-run", data_root=str(changed_root),
                     data_manifest=str(changed_root/"manifest.json"))
    altered = run_fixed_selection(changed)
    assert result(base)["selected_parameters"] == result(altered)["selected_parameters"]
    assert result(base)["candidate_table"][0]["validation_metrics"] == result(altered)["candidate_table"][0]["validation_metrics"]


def test_fixed_test_is_only_selected_nonvalidation_child(tmp_path):
    root=run_fixed_selection(config(tmp_path)); payload=result(root)
    assert len(list((root/"frozen_test").glob("*/artifact_manifest.json"))) == 1
    assert payload["selected_candidate_id"] in {r["candidate_id"] for r in payload["candidate_table"]}
    assert all(len(list((root/"candidates"/r["candidate_id"]/"result").glob("*/artifact_manifest.json"))) <= 1
               for r in payload["candidate_table"])


def test_deterministic_ties_use_canonical_parameters(tmp_path):
    raw=json.loads(FIXED.read_text()); raw["parameter_selection"]["parameter_grid"]={"top_k":[2,1]}
    raw["parameter_selection"]["selection_objective"]="max_drawdown"
    raw["parameter_selection"]["objective_direction"]="minimize"
    raw["parameter_selection"]["minimum_observations"]=1
    raw["split_definitions"].update(validation_start="2024-01-29",validation_end="2024-01-29",
                                    test_start="2024-01-30")
    cfg=config(tmp_path, **raw)
    assert result(run_fixed_selection(cfg))["selected_parameters"] == {"top_k":1}


def test_continue_records_failure_and_never_scores_zero(tmp_path):
    raw=json.loads(FIXED.read_text()); raw["parameter_selection"]["parameter_grid"]={"top_k":[0,1]}
    cfg=config(tmp_path, **raw); table=result(run_fixed_selection(cfg))["candidate_table"]
    failed=[r for r in table if r["error"]]
    assert failed and failed[0]["validation_metrics"] == {} and failed[0]["eligible"]


def test_fail_fast_propagates_candidate_failure(tmp_path):
    raw=json.loads(FIXED.read_text()); raw["parameter_selection"]["parameter_grid"]={"top_k":[0,1]}
    raw["parameter_selection"]["failure_policy"]="fail_fast"
    with pytest.raises(ValueError): run_fixed_selection(config(tmp_path, **raw))


def test_candidate_identity_covers_all_result_affecting_inputs(tmp_path):
    cfg=config(tmp_path); manifest=SnapshotManifest.load(cfg.data_manifest)
    provenance=experiment_metadata(cfg,cfg.data_manifest,git_sha="a"*40,dependency_versions={"x":"1"})
    parent=experiment_identity(cfg,manifest,provenance)
    fold=Fold((date(2024,1,24),),(date(2024,1,25),),(date(2024,1,26),))
    base=candidate_identity(parent,cfg,manifest,{"top_k":1},fold,provenance)["candidate_id"]
    variants=[(replace(cfg,fee_parameters={"commission_rate":.02}),manifest,{"top_k":1},fold,provenance),
      (replace(cfg,slippage_parameters={"slippage_rate":.02}),manifest,{"top_k":1},fold,provenance),
      (replace(cfg,execution_parameters={"execution_timing":"next_open","lot_size":1}),manifest,{"top_k":1},fold,provenance),
      (cfg,manifest,{"top_k":2},fold,provenance),
      (cfg,manifest,{"top_k":1},Fold((date(2024,1,23),),(date(2024,1,25),),(date(2024,1,26),)),provenance),
      (cfg,manifest,{"top_k":1},fold,{**provenance,"source_tree_sha256":"b"*64})]
    assert all(candidate_identity(parent,c,m,p,f,prov)["candidate_id"] != base for c,m,p,f,prov in variants)
    altered_entry=replace(manifest.entries[0],sha256="f"*64); altered=replace(manifest,entries=(altered_entry,*manifest.entries[1:]))
    assert candidate_identity(parent,cfg,altered,{"top_k":1},fold,provenance)["candidate_id"] != base


def test_sealed_tree_rejects_mutation_missing_and_additional_children(tmp_path):
    root=run_fixed_selection(config(tmp_path)); validate_artifact_directory(root)
    candidate=next((root/"candidates").iterdir()); evaluation=candidate/"evaluation.json"
    original=evaluation.read_bytes(); evaluation.write_bytes(original+b" ")
    with pytest.raises(ValueError,match="hash mismatch"): validate_artifact_directory(root)
    evaluation.write_bytes(original)
    rogue=root/"rogue"; rogue.mkdir()
    with pytest.raises(ValueError,match="undeclared|contract"): validate_artifact_directory(root)
    rogue.rmdir(); frozen=next((root/"frozen_test").iterdir()); moved=root/"saved"; frozen.rename(moved)
    with pytest.raises(ValueError,match="contract|mismatch"): validate_artifact_directory(root)


def test_partial_candidate_and_frozen_test_are_recomputed(tmp_path):
    cfg=config(tmp_path); root=run_fixed_selection(cfg); payload=result(root)
    (root/"artifact_manifest.json").unlink()
    candidate=root/"candidates"/payload["candidate_table"][0]["candidate_id"]
    shutil.rmtree(candidate); candidate.mkdir(); (candidate/"partial").write_text("x")
    frozen_parent=root/"frozen_test"; shutil.rmtree(frozen_parent); frozen_parent.mkdir(); (frozen_parent/"partial").mkdir()
    resumed=run_fixed_selection(cfg)
    validate_artifact_directory(resumed)


def test_partial_fold_and_stitched_publication_are_recovered(tmp_path):
    cfg=config(tmp_path,WALK); root=run_walk_forward(cfg); (root/"artifact_manifest.json").unlink()
    (root/"stitched_oos_equity.csv").write_text("partial")
    fold=root/"fold-0000"; (fold/"artifact_manifest.json").unlink()
    resumed=run_walk_forward(cfg); validate_artifact_directory(resumed)
    assert len(rows(resumed/"stitched_oos_equity.csv")) == len({r["date"] for r in rows(resumed/"stitched_oos_equity.csv")})


def test_candidate_and_fold_limits_and_overlap_are_rejected_before_run(tmp_path):
    raw=json.loads(FIXED.read_text()); raw["resource_limits"]["maximum_candidate_count"]=1
    with pytest.raises(ValueError,match="maximum_candidate_count"): config(tmp_path,**raw)
    walk=config(tmp_path/"w",WALK,resource_limits={"maximum_candidate_count":2,"maximum_folds":1,"execution":"serial"})
    with pytest.raises(ValueError,match="maximum_folds"): run_walk_forward(walk)
    overlap=config(tmp_path/"o",WALK,split_definitions={"type":"rolling","train_observations":3,
      "validation_observations":2,"test_observations":3,"step_observations":1,"warmup_observations":2})
    manifest=SnapshotManifest.load(overlap.data_manifest)
    from kelaode.selection_runner import _calendar
    with pytest.raises(ValueError,match="overlapping"): _folds(overlap,_calendar(overlap,manifest))


def test_zero_wall_clock_budget_stops_before_candidate(tmp_path):
    cfg=config(tmp_path,resource_limits={"maximum_candidate_count":2,"execution":"serial","wall_clock_seconds":0})
    with pytest.raises(TimeoutError,match="budget"): run_fixed_selection(cfg)


def test_walk_artifacts_reconstruct_local_rebased_return_and_drawdown(tmp_path):
    root=run_walk_forward(config(tmp_path,WALK)); stitched=rows(root/"stitched_oos_equity.csv")
    assembled=[]
    for fold in sorted(root.glob("fold-*")):
        local=rows(fold/"fold_local_equity.csv"); rebased=rows(fold/"fold_rebased_equity.csv")
        assert [r["date"] for r in local] == [r["date"] for r in rebased]
        assembled.extend(rebased)
    assert assembled == stitched
    values=[float(r["equity"]) for r in stitched]
    expected_return=values[-1]/values[0]-1; peak=values[0]; max_dd=0
    for value in values: peak=max(peak,value); max_dd=min(max_dd,value/peak-1)
    payload=result(root)
    assert payload["stitched_metrics"]["total_return"] == pytest.approx(expected_return)
    assert payload["stitched_metrics"]["max_drawdown"] == pytest.approx(max_dd)


def test_history_only_warmup_has_no_orders_or_positions_but_changes_signal():
    class WarmStrategy:
        def __init__(self): self.signals=[]
        def target_weights(self,index,today,view,snapshot):
            history=view.history("X","close",2); target={"X":1.0 if len(history)==2 and history[-1]>history[-2] else 0.0}
            self.signals.append((today,target["X"])); return target
    bars=lambda first:[DailyBar(date(2024,1,1),first,first,first,first,1000),DailyBar(date(2024,1,2),10,10,10,10,1000),DailyBar(date(2024,1,3),10,10,10,10,1000)]
    cfg=PortfolioBacktestConfig(initial_cash=1000,lot_size=1,commission_rate=0,minimum_commission=0)
    rising=WarmStrategy(); result1=PortfolioBacktester(cfg).run({"X":bars(5)},rising,execution_start=date(2024,1,2))
    falling=WarmStrategy(); PortfolioBacktester(cfg).run({"X":bars(15)},falling,execution_start=date(2024,1,2))
    assert result1.positions_by_date[date(2024,1,1)]["X"] == 0
    assert not [o for o in result1.orders if o.trade_date < date(2024,1,2)]
    assert rising.signals[1][1] != falling.signals[1][1]


def test_cli_mode_and_output_are_configuration_controlled(tmp_path):
    cfg=config(tmp_path); path=tmp_path/"fixed.json"; path.write_text(cfg.to_json())
    completed=subprocess.run([sys.executable,"-m","kelaode.experiment_cli","grid-search","--config",str(path)],
      cwd=REPO,text=True,capture_output=True,env={"PYTHONPATH":str(REPO/"src")})
    assert completed.returncode == 0 and str(tmp_path/"results") in completed.stdout
    wrong=subprocess.run([sys.executable,"-m","kelaode.experiment_cli","walk-forward","--config",str(path)],
      cwd=REPO,text=True,capture_output=True,env={"PYTHONPATH":str(REPO/"src")})
    assert wrong.returncode == 2 and "experiment_mode=walk_forward" in wrong.stderr


def test_cost_analyses_are_separate_reconcile_and_monotone(tmp_path):
    costs={"closed_loop":{"stress":{"commission_rate":.003,"slippage_rate":.002}},
           "fixed_path":{"base":{},"low":{"commission_rate":.001,"slippage_rate":.001},
                         "high":{"commission_rate":.01,"minimum_commission":5,"slippage_rate":.01}}}
    root=run_fixed_selection(config(tmp_path,cost_analysis=costs)); analysis=result(root)["cost_analysis"]
    assert analysis["closed_loop"]["stress"]["label"] == "closed-loop execution rerun"
    assert analysis["fixed_path"]["high"]["label"] == "fixed frozen fill-path repricing"
    assert analysis["fixed_path_contract"]["base_fill_path_sha256"]
    assert analysis["fixed_path_contract"]["starting_positions"] == {}
    assert analysis["fixed_path"]["high"]["final_equity"] <= analysis["fixed_path"]["low"]["final_equity"]


def test_future_fold_mutation_cannot_change_first_fold_selection_or_orders(tmp_path):
    first=run_walk_forward(config(tmp_path/"base",WALK)); first_payload=result(first)["folds"][0]
    changed_root=copy_snapshot(tmp_path/"future","2024-02-08",1.4)
    changed=run_walk_forward(config(tmp_path/"changed",WALK,data_root=str(changed_root),
                        data_manifest=str(changed_root/"manifest.json")))
    changed_payload=result(changed)["folds"][0]
    assert first_payload["selected_parameters"] == changed_payload["selected_parameters"]
    first_orders=rows(first/"fold-0000"/first_payload["frozen_test_bundle"]/"orders.csv")
    changed_orders=rows(changed/"fold-0000"/changed_payload["frozen_test_bundle"]/"orders.csv")
    assert first_orders == changed_orders


def test_stale_candidate_identity_and_missing_fold_are_rejected(tmp_path):
    fixed=run_fixed_selection(config(tmp_path/"fixed")); candidate=next((fixed/"candidates").iterdir())
    identity=candidate/"identity.json"; identity.write_text(identity.read_text()+" ")
    with pytest.raises(ValueError,match="hash mismatch"):
        validate_artifact_directory(fixed)
    walk=run_walk_forward(config(tmp_path/"walk",WALK)); shutil.rmtree(walk/"fold-0000")
    with pytest.raises(ValueError,match="contract|mismatch"):
        validate_artifact_directory(walk)


def test_registered_fittable_strategy_is_rejected_before_snapshot_loading(monkeypatch, tmp_path):
    import kelaode.strategy_registry as registry
    original=registry.STRATEGY_REGISTRY["SITMomentumRotationStrategy"]
    monkeypatch.setattr(registry,"STRATEGY_REGISTRY",{"SITMomentumRotationStrategy":replace(original,fit_applicable=True)})
    missing=config(tmp_path,data_manifest=str(tmp_path/"does-not-exist.json"))
    with pytest.raises(ValueError,match="fittable"):
        run_fixed_selection(missing)
