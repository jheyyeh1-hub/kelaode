"""Schema-2.0 validation-only selection and out-of-sample orchestration.

The module deliberately builds on :func:`runner.run_experiment`; it does not
implement another strategy or execution pipeline.
"""
from __future__ import annotations

import csv
import hashlib
import itertools
import json
import shutil
import time
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from .experiment import (ExperimentConfig, ExpandingWalkForward, Fold,
                         RollingWalkForward, experiment_identity, experiment_metadata)
from .experiment_metrics import performance_metrics
from .cost_analysis import ReplayFill, fixed_path_cost_replay
from .runner import _aligned_data, run_experiment
from .snapshot import SnapshotManifest, canonical_json, sha256_file

CANDIDATE_SCHEMA_VERSION = "2.0-candidate-1"
SELECTION_BUNDLE_VERSION = "2.0-selection-1"


def _json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _manifest(root: Path) -> None:
    names = sorted(p.name for p in root.iterdir() if p.is_file() and p.name != "artifact_manifest.json")
    _json(root / "artifact_manifest.json", {"schema_version": SELECTION_BUNDLE_VERSION,
          "artifacts": {name: sha256_file(root / name) for name in names}})


def validate_artifact_directory(root: Path) -> None:
    """Reject missing, modified, and additional undeclared direct artifacts."""
    try:
        manifest = json.loads((root / "artifact_manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"artifact directory is incomplete: {root}") from exc
    declared = manifest.get("artifacts")
    actual = {p.name for p in root.iterdir() if p.is_file()} - {"artifact_manifest.json"}
    if manifest.get("schema_version") != SELECTION_BUNDLE_VERSION or not isinstance(declared, dict) or set(declared) != actual:
        raise ValueError(f"artifact contract mismatch: {root}")
    for name, digest in declared.items():
        if sha256_file(root / name) != digest:
            raise ValueError(f"artifact hash mismatch: {root / name}")
    # Every descendant result directory is an independently sealed contract.
    for child_manifest in root.glob("**/artifact_manifest.json"):
        child = child_manifest.parent
        if child == root:
            continue
        value = json.loads(child_manifest.read_text(encoding="utf-8"))
        child_declared = value.get("artifacts", {})
        child_actual = {p.name for p in child.iterdir() if p.is_file()} - {"artifact_manifest.json"}
        if set(child_declared) != child_actual:
            raise ValueError(f"nested artifact contract mismatch: {child}")
        for name, digest in child_declared.items():
            if sha256_file(child / name) != digest:
                raise ValueError(f"nested artifact hash mismatch: {child / name}")


def _parent(config: ExperimentConfig, manifest: SnapshotManifest) -> tuple[dict, dict]:
    provenance = experiment_metadata(config, config.data_manifest)
    return experiment_identity(config, manifest, provenance), provenance


def candidate_identity(parent: Mapping[str, Any], config: ExperimentConfig, manifest: SnapshotManifest,
                       parameters: Mapping[str, Any], fold: Fold, provenance: Mapping[str, Any]) -> dict:
    payload = {"candidate_schema_version": CANDIDATE_SCHEMA_VERSION,
               "parent_experiment_id": parent["experiment_id"],
               "strategy": {"class": config.strategy_class, "parameters": dict(parameters)},
               "manifest_hash": manifest.hash, "input_hashes": [e.sha256 for e in manifest.entries],
               "boundaries": {"train": [str(x) for x in fold.train], "validation": [str(x) for x in fold.validation],
                              "warmup": [str(x) for x in fold.warmup]},
               "costs": {"fees": dict(config.fee_parameters), "slippage": dict(config.slippage_parameters)},
               "execution": dict(config.execution_parameters), "constraints": dict(config.constraint_parameters),
               "benchmark": dict(config.benchmark_definitions),
               "provenance": {k: v for k, v in provenance.items() if k != "created_at_utc"}}
    return {"candidate_id": hashlib.sha256(canonical_json(payload).encode()).hexdigest(), "canonical_inputs": payload}


def _combinations(config: ExperimentConfig) -> tuple[dict, ...]:
    grid = config.parameter_selection["parameter_grid"]
    keys = sorted(grid)
    return tuple(dict(zip(keys, values)) for values in itertools.product(*(grid[k] for k in keys)))


def _valid_parameters(params: Mapping[str, Any], constraints: Sequence[Mapping[str, Any]]) -> bool:
    operators = {"eq": lambda a,b:a==b, "ne": lambda a,b:a!=b, "lt": lambda a,b:a<b,
                 "le": lambda a,b:a<=b, "gt": lambda a,b:a>b, "ge": lambda a,b:a>=b}
    for rule in constraints:
        if not isinstance(rule, Mapping) or set(rule) != {"parameter", "operator", "value"} or rule["operator"] not in operators:
            raise ValueError("each parameter constraint requires parameter, operator, and value")
        if rule["parameter"] not in params or not operators[rule["operator"]](params[rule["parameter"]], rule["value"]):
            return False
    return True


def _child(config: ExperimentConfig, parameters: Mapping[str, Any], start: date, end: date, output: Path) -> Path:
    merged = {**config.strategy_parameters, **parameters}
    child = replace(config, experiment_name=f"{config.experiment_name}-frozen-child", experiment_mode="run",
                    strategy_parameters=merged, start_date=str(start), end_date=str(end), output_directory=str(output),
                    split_definitions={"type": "none", "reason": "frozen schema-2.0 selection child"},
                    parameter_selection={}, resource_limits={}, cost_analysis={})
    return run_experiment(child)


def _metrics(bundle: Path, dates: Sequence[date]) -> dict[str, float]:
    wanted = {str(x) for x in dates}
    with (bundle / "equity_curve.csv").open(newline="", encoding="utf-8") as stream:
        values = [float(r["equity"]) for r in csv.DictReader(stream) if r["date"] in wanted]
    if not values:
        raise ValueError("candidate produced no observations in the validation interval")
    return performance_metrics(values)


def _rank(config: ExperimentConfig, rows: list[dict]) -> dict:
    eligible = [r for r in rows if not r.get("error") and r.get("eligible")]
    if not eligible:
        raise ValueError("no eligible parameter candidate")
    objective = config.parameter_selection["selection_objective"]
    direction = config.parameter_selection["objective_direction"]
    tie_rules = config.parameter_selection["tie_break_rules"]
    if any(objective not in r["validation_metrics"] for r in eligible):
        raise ValueError(f"selection objective is missing: {objective}")
    def key(row):
        score = float(row["validation_metrics"][objective])
        primary = -score if direction == "maximize" else score
        tie = []
        for rule in tie_rules:
            if rule == "canonical_parameters": tie.append(canonical_json(row["parameters"]))
            elif rule.startswith("parameter:"): tie.append(canonical_json(row["parameters"].get(rule.split(":",1)[1])))
            elif rule.startswith("metric:"): tie.append(float(row["validation_metrics"].get(rule.split(":",1)[1], float("inf"))))
            else: raise ValueError(f"unsupported tie-break rule: {rule}")
        return (primary, *tie, canonical_json(row["parameters"]))
    return min(eligible, key=key)


def _evaluate(config: ExperimentConfig, manifest: SnapshotManifest, parent: dict, provenance: dict,
              fold: Fold, root: Path, started: float) -> tuple[list[dict], dict]:
    rows = []
    candidate_root = root / "candidates"
    candidate_root.mkdir(parents=True, exist_ok=True)
    for params in _combinations(config):
        budget = config.resource_limits.get("wall_clock_seconds")
        if budget is not None and time.monotonic() - started >= float(budget):
            raise TimeoutError("selection wall-clock budget exhausted between candidates")
        identity = candidate_identity(parent, config, manifest, params, fold, provenance)
        directory = candidate_root / identity["candidate_id"]
        if directory.exists() and not (directory / "artifact_manifest.json").is_file():
            shutil.rmtree(directory)
        if directory.exists():
            validate_artifact_directory(directory)
            saved = json.loads((directory / "identity.json").read_text())
            if canonical_json(saved) != canonical_json(identity): raise ValueError("stale candidate cache identity")
            row = json.loads((directory / "evaluation.json").read_text()); row["cached"] = True
            rows.append(row); continue
        directory.mkdir()
        row = {"candidate_id": identity["candidate_id"], "parameters": params, "validation_metrics": {},
               "eligible": False, "error": None, "cached": False}
        try:
            row["eligible"] = _valid_parameters(params, config.parameter_selection["parameter_constraints"])
            if row["eligible"]:
                minimum = int(config.parameter_selection["minimum_observations"])
                if len(fold.validation) < minimum: raise ValueError("validation interval has fewer than minimum_observations")
                start = min((*fold.warmup, *fold.validation))
                bundle = _child(config, params, start, max(fold.validation), directory / "result")
                row["validation_metrics"] = _metrics(bundle, fold.validation)
                row["result_bundle"] = str(bundle.relative_to(directory))
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
            if config.parameter_selection["failure_policy"] == "fail_fast":
                shutil.rmtree(directory, ignore_errors=True); raise
        _json(directory / "identity.json", identity); _json(directory / "resolved_configuration.json", asdict(config))
        _json(directory / "evaluation.json", row); _manifest(directory)
        rows.append(row)
    return rows, _rank(config, rows)


def _calendar(config: ExperimentConfig, manifest: SnapshotManifest) -> tuple[date, ...]:
    data = _aligned_data(config, manifest)
    calendars = tuple({b.trade_date for b in bars} for bars in data.values())
    combined = set.intersection(*calendars) if config.data_alignment_mode == "intersection" else set.union(*calendars)
    return tuple(sorted(combined))


def _fixed_fold(config: ExperimentConfig, calendar: Sequence[date]) -> Fold:
    s = config.split_definitions
    within = lambda a,b: tuple(d for d in calendar if date.fromisoformat(a) <= d <= date.fromisoformat(b))
    train, validation, test = within(s["train_start"],s["train_end"]), within(s["validation_start"],s["validation_end"]), within(s["test_start"],s["test_end"])
    if not train or not validation or not test:
        raise ValueError("fixed split train, validation, and frozen test intervals must be nonempty")
    warm = tuple(d for d in calendar if d < validation[0])[-int(s["warmup_observations"]):] if s["warmup_observations"] else ()
    return Fold(train, validation, test, warm)


def _write_parent(root: Path, config: ExperimentConfig, identity: dict, provenance: dict, payload: dict) -> None:
    _json(root / "configuration.json", asdict(config)); _json(root / "identity.json", identity)
    _json(root / "runtime.json", provenance); _json(root / "result.json", payload); _manifest(root)


def _cost_results(config: ExperimentConfig, parameters: Mapping[str, Any], fold: Fold,
                  base_bundle: Path, root: Path) -> dict:
    """Run post-selection stresses without ever re-entering candidate selection."""
    output = {"closed_loop": {}, "fixed_path": {}}
    for name, costs in config.cost_analysis.get("closed_loop", {}).items():
        stressed = replace(config,
            fee_parameters={**config.fee_parameters, **{k:v for k,v in costs.items() if k != "slippage_rate"}},
            slippage_parameters={**config.slippage_parameters, **{k:v for k,v in costs.items() if k == "slippage_rate"}})
        bundle = _child(stressed, parameters, min((*fold.warmup,*fold.test)), max(fold.test), root/"closed_loop"/name)
        output["closed_loop"][name] = {"label":"closed-loop execution rerun", "metrics":_metrics(bundle,fold.test),
                                               "bundle":str(bundle.relative_to(root))}
    if config.cost_analysis.get("fixed_path"):
        with (base_bundle/"fills.csv").open(newline="",encoding="utf-8") as stream:
            fills_rows=list(csv.DictReader(stream))
        base_slip=float(config.slippage_parameters.get("slippage_rate",0.0))
        fills=[ReplayFill(r["symbol"],r["side"],int(r["quantity"]),
                          float(r["price"])/(1+(base_slip if r["side"]=="buy" else -base_slip))) for r in fills_rows]
        with (base_bundle/"marks.csv").open(newline="",encoding="utf-8") as stream:
            marks_rows=[r for r in csv.DictReader(stream) if r["date"]==str(max(fold.test)) and r["available"]=="True"]
        marks={r["symbol"]:float(r["close"]) for r in marks_rows}
        for name,costs in config.cost_analysis["fixed_path"].items():
            output["fixed_path"][name]={"label":"fixed frozen fill-path repricing",
                "final_equity":fixed_path_cost_replay(config.initial_cash,fills,
                    commission_rate=float(costs.get("commission_rate",config.fee_parameters.get("commission_rate",0))),
                    minimum_commission=float(costs.get("minimum_commission",config.fee_parameters.get("minimum_commission",0))),
                    slippage_rate=float(costs.get("slippage_rate",config.slippage_parameters.get("slippage_rate",0))),
                    final_prices=marks)}
    return output


def run_fixed_selection(config: ExperimentConfig) -> Path:
    if config.experiment_mode != "fixed_selection": raise ValueError("fixed selection requires experiment_mode=fixed_selection")
    started = time.monotonic(); manifest = SnapshotManifest.load(config.data_manifest)
    manifest.validate(config.data_root, expected_symbols=config.universe, allow_mixed_adjustments=config.allow_mixed_adjustments)
    parent, provenance = _parent(config, manifest); root = Path(config.output_directory) / parent["experiment_id"]
    if (root / "artifact_manifest.json").exists(): validate_artifact_directory(root); return root
    root.mkdir(parents=True, exist_ok=True); fold = _fixed_fold(config, _calendar(config, manifest))
    rows, selected = _evaluate(config, manifest, parent, provenance, fold, root, started)
    # This is the sole frozen-test invocation.
    test_bundle = _child(config, selected["parameters"], min((*fold.warmup, *fold.test)), max(fold.test), root / "frozen_test")
    payload = {"mode":"fixed_selection", "boundaries": {k:[str(x) for x in getattr(fold,k)] for k in ("train","validation","warmup","test")},
               "candidate_table":rows, "selected_parameters":selected["parameters"],
               "validation_metrics":selected["validation_metrics"], "test_metrics":_metrics(test_bundle, fold.test),
               "frozen_test_bundle":str(test_bundle.relative_to(root)),
               "cost_analysis":_cost_results(config,selected["parameters"],fold,test_bundle,root),
               "selection_rationale":{"objective":config.parameter_selection["selection_objective"],"direction":config.parameter_selection["objective_direction"],"tie_break_rules":config.parameter_selection["tie_break_rules"]}}
    _write_parent(root, config, parent, provenance, payload); return root


def _folds(config: ExperimentConfig, calendar: Sequence[date]) -> tuple[Fold,...]:
    s=config.split_definitions; cls=RollingWalkForward if s["type"]=="rolling" else ExpandingWalkForward
    splitter=cls(s["train_observations"],s["validation_observations"],s["test_observations"],s["step_observations"],s["warmup_observations"])
    folds=splitter.split(calendar)
    if len(folds)>config.resource_limits["maximum_folds"]: raise ValueError("walk-forward exceeds maximum_folds")
    seen=set()
    for fold in folds:
        if seen.intersection(fold.test): raise ValueError("overlapping OOS dates")
        seen.update(fold.test)
    if not folds: raise ValueError("walk-forward produced no folds")
    return folds


def run_walk_forward(config: ExperimentConfig) -> Path:
    if config.experiment_mode != "walk_forward": raise ValueError("walk-forward requires experiment_mode=walk_forward")
    started=time.monotonic(); manifest=SnapshotManifest.load(config.data_manifest)
    manifest.validate(config.data_root, expected_symbols=config.universe, allow_mixed_adjustments=config.allow_mixed_adjustments)
    parent,provenance=_parent(config,manifest); root=Path(config.output_directory)/parent["experiment_id"]
    if (root/"artifact_manifest.json").exists(): validate_artifact_directory(root); return root
    root.mkdir(parents=True,exist_ok=True); reports=[]; stitched=[]
    for number,fold in enumerate(_folds(config,_calendar(config,manifest))):
        fold_root=root/f"fold-{number:04d}"; fold_root.mkdir(exist_ok=True)
        rows,selected=_evaluate(config,manifest,parent,provenance,fold,fold_root,started)
        bundle=_child(config,selected["parameters"],min((*fold.warmup,*fold.test)),max(fold.test),fold_root/"frozen_test")
        metrics=_metrics(bundle,fold.test)
        with (bundle/"equity_curve.csv").open(newline="",encoding="utf-8") as f:
            stitched.extend(r for r in csv.DictReader(f) if r["date"] in {str(x) for x in fold.test})
        report={"fold":number,"boundaries":{k:[str(x) for x in getattr(fold,k)] for k in ("train","validation","warmup","test")},
                "candidate_table":rows,"selected_parameters":selected["parameters"],"validation_metrics":selected["validation_metrics"],
                "test_metrics":metrics,"frozen_test_bundle":str(bundle.relative_to(fold_root)),
                "cost_analysis":_cost_results(config,selected["parameters"],fold,bundle,fold_root)}
        _json(fold_root/"fold_result.json",report); _json(fold_root/"configuration.json",asdict(config)); _json(fold_root/"identity.json",parent); _manifest(fold_root); reports.append(report)
    stitched.sort(key=lambda r:r["date"])
    with (root/"stitched_oos_equity.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=["date","equity"]); w.writeheader(); w.writerows(stitched)
    vals=[float(x["equity"]) for x in stitched]; peak=0.; draw=[]
    for row in stitched: peak=max(peak,float(row["equity"])); draw.append({"date":row["date"],"drawdown":float(row["equity"])/peak-1})
    with (root/"stitched_oos_drawdown.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=["date","drawdown"]); w.writeheader(); w.writerows(draw)
    payload={"mode":"walk_forward","folds":reports,"parameter_switch_history":[{"fold":r["fold"],"parameters":r["selected_parameters"]} for r in reports],"stitched_metrics":performance_metrics(vals)}
    _write_parent(root,config,parent,provenance,payload); return root
