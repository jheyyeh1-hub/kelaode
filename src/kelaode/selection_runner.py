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
from .strategy_registry import require_no_fit_strategy

CANDIDATE_SCHEMA_VERSION = "2.0-candidate-1"
SELECTION_BUNDLE_VERSION = "2.0-selection-1"


def _json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _manifest(root: Path, *, children: Mapping[str, Path] | None = None,
              expected_counts: Mapping[str, int] | None = None) -> None:
    names = sorted(p.name for p in root.iterdir() if p.is_file() and p.name != "artifact_manifest.json")
    child_contract = {}
    for relative, child in sorted((children or {}).items()):
        manifest = child / "artifact_manifest.json"
        if not manifest.is_file():
            raise ValueError(f"child has no artifact manifest: {child}")
        child_contract[relative] = sha256_file(manifest)
    _json(root / "artifact_manifest.json", {"schema_version": SELECTION_BUNDLE_VERSION,
          "artifacts": {name: sha256_file(root / name) for name in names},
          "children": child_contract, "expected_counts": dict(expected_counts or {})})


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
    children = manifest.get("children")
    if not isinstance(children, dict) or not isinstance(manifest.get("expected_counts"), dict):
        raise ValueError(f"artifact child contract missing: {root}")
    declared_paths = set(children)
    counts = manifest["expected_counts"]
    observed = {"folds": sum(path.startswith("fold-") for path in declared_paths),
                "candidates": sum("candidates/" in path for path in declared_paths),
                "frozen_tests": sum("frozen_test/" in path for path in declared_paths),
                "cost_scenarios": sum("closed_loop/" in path for path in declared_paths)}
    for name, expected in counts.items():
        if name in observed and observed[name] != expected:
            raise ValueError(f"artifact expected {expected} {name}, found {observed[name]}")
    actual_paths = {str(p.parent.relative_to(root)) for p in root.glob("**/artifact_manifest.json")
                    if p.parent != root}
    # Descendants declared by a declared child belong to that child's contract.
    top_actual = {p for p in actual_paths if not any(p != q and p.startswith(q + "/") for q in actual_paths)}
    top_declared = {p for p in declared_paths if not any(p != q and p.startswith(q + "/") for q in declared_paths)}
    if top_actual != top_declared:
        raise ValueError(f"artifact child directory contract mismatch: {root}")
    allowed_ancestors = {part for path in top_declared for part in
                         ("/".join(path.split("/")[:i]) for i in range(1, len(path.split("/"))))}
    for directory in (p for p in root.iterdir() if p.is_dir()):
        relative = str(directory.relative_to(root))
        if relative not in allowed_ancestors and relative not in top_declared:
            raise ValueError(f"additional undeclared child directory: {directory}")
    for relative, digest in children.items():
        child = root / relative
        child_manifest = child / "artifact_manifest.json"
        if not child.is_dir() or not child_manifest.is_file() or sha256_file(child_manifest) != digest:
            raise ValueError(f"child artifact manifest mismatch: {child}")
        value = json.loads(child_manifest.read_text(encoding="utf-8"))
        if value.get("schema_version") == SELECTION_BUNDLE_VERSION:
            validate_artifact_directory(child)
        else:
            child_declared = value.get("artifacts", {})
            child_actual = {p.name for p in child.iterdir() if p.is_file()} - {"artifact_manifest.json"}
            if (any(p.is_dir() for p in child.iterdir()) or set(child_declared) != child_actual or
                    any(sha256_file(child/name) != h for name,h in child_declared.items())):
                raise ValueError(f"nested artifact contract mismatch: {child}")


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


def _child(config: ExperimentConfig, parameters: Mapping[str, Any], start: date, end: date,
           execution_start: date, output: Path) -> Path:
    merged = {**config.strategy_parameters, **parameters}
    child = replace(config, experiment_name=f"{config.experiment_name}-frozen-child", experiment_mode="run",
                    strategy_parameters=merged, start_date=str(start), end_date=str(end), output_directory=str(output),
                    split_definitions={"type": "none", "reason": "frozen schema-2.0 selection child"},
                    parameter_selection={}, resource_limits={}, cost_analysis={},
                    warmup_policy="history_only", execution_start_date=str(execution_start))
    output.mkdir(parents=True, exist_ok=True)
    for existing in output.iterdir():
        if existing.is_dir() and not (existing / "artifact_manifest.json").is_file():
            shutil.rmtree(existing)
    return run_experiment(child)


def _metrics(bundle: Path, dates: Sequence[date]) -> dict[str, float]:
    wanted = {str(x) for x in dates}
    with (bundle / "equity_curve.csv").open(newline="", encoding="utf-8") as stream:
        values = [float(r["equity"]) for r in csv.DictReader(stream) if r["date"] in wanted]
    if not values:
        raise ValueError("candidate produced no observations in the validation interval")
    with (bundle / "trades.csv").open(newline="", encoding="utf-8") as stream:
        trades = [{**row, "notional": float(row["price"]) * int(row["quantity"])}
                  for row in csv.DictReader(stream) if row["date"] in wanted]
    return performance_metrics(values, trades)


def stitch_oos_equity(fold_paths: Sequence[Sequence[Mapping[str, Any]]],
                      initial_capital: float) -> tuple[dict[str, float], ...]:
    """Compound independent fold-local return paths without capital resets.

    The first local observation anchors each fold to the prior stitched capital.
    A one-observation fold therefore contributes a zero return. Nonpositive or
    nonfinite anchors/observations are rejected because ratios are undefined.
    """
    from math import isfinite
    if not isfinite(initial_capital) or initial_capital <= 0:
        raise ValueError("stitched initial capital must be positive and finite")
    ending = float(initial_capital); output = []; previous_date = None
    for path in fold_paths:
        if not path:
            raise ValueError("each OOS fold must contain an equity observation")
        local = [(str(row["date"]), float(row["equity"])) for row in path]
        if any(not isfinite(value) or value <= 0 for _, value in local):
            raise ValueError("fold-local OOS equity must be positive and finite")
        if any(local[i][0] >= local[i+1][0] for i in range(len(local)-1)):
            raise ValueError("fold-local OOS dates must be ordered and unique")
        anchor = local[0][1]
        for day, value in local:
            if previous_date is not None and day <= previous_date:
                raise ValueError("stitched OOS dates must be globally ordered and unique")
            rebased = ending * value / anchor
            output.append({"date": day, "equity": rebased})
            previous_date = day
        ending = output[-1]["equity"]
    return tuple(output)


def _equity_rows(bundle: Path, dates: Sequence[date]) -> list[dict[str, Any]]:
    wanted = {str(x) for x in dates}
    with (bundle / "equity_curve.csv").open(newline="", encoding="utf-8") as stream:
        return [{"date": r["date"], "equity": float(r["equity"])}
                for r in csv.DictReader(stream) if r["date"] in wanted]


def _write_equity(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["date", "equity"])
        writer.writeheader(); writer.writerows(rows)


def _remove_undeclared_direct_files(root: Path, allowed: set[str]) -> None:
    for path in root.iterdir():
        if path.is_file() and path.name not in allowed:
            path.unlink()


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
            elif rule == "lower_complexity":
                p = row["parameters"]
                tie.append(int(p.get("trend_window") is not None) + int(p.get("volatility_lookback") is not None))
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
                bundle = _child(config, params, start, max(fold.validation), min(fold.validation), directory / "result")
                row["validation_metrics"] = _metrics(bundle, fold.validation)
                operators = {"lt":lambda a,b:a<b, "le":lambda a,b:a<=b,
                             "gt":lambda a,b:a>b, "ge":lambda a,b:a>=b}
                for rule in config.parameter_selection.get("metric_constraints", []):
                    actual = row["validation_metrics"].get(rule["metric"])
                    if actual is None or not operators[rule["operator"]](float(actual), float(rule["value"])):
                        row["eligible"] = False
                        row.setdefault("ineligibility_reasons", []).append(
                            f"{rule['metric']} {rule['operator']} {rule['value']} not satisfied (actual={actual})")
                row["result_bundle"] = str(bundle.relative_to(directory))
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
            shutil.rmtree(directory / "result", ignore_errors=True)
            if config.parameter_selection["failure_policy"] == "fail_fast":
                shutil.rmtree(directory, ignore_errors=True); raise
        row.update({"fit_applicable": False,
                    "fit_reason": "registered fixed-rule strategy; train boundary is recorded but unused"})
        _json(directory / "identity.json", identity); _json(directory / "resolved_configuration.json", asdict(config))
        _json(directory / "evaluation.json", row)
        children = ({row["result_bundle"]: directory / row["result_bundle"]} if row.get("result_bundle") else {})
        _manifest(directory, children=children, expected_counts={"result_bundles": len(children)})
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


def _write_parent(root: Path, config: ExperimentConfig, identity: dict, provenance: dict, payload: dict,
                  *, children: Mapping[str, Path], expected_counts: Mapping[str, int]) -> None:
    allowed={"configuration.json","identity.json","runtime.json","result.json","artifact_manifest.json"}
    if payload.get("mode") == "walk_forward":
        allowed.update({"stitched_oos_equity.csv","stitched_oos_drawdown.csv"})
    _remove_undeclared_direct_files(root,allowed)
    _json(root / "configuration.json", asdict(config)); _json(root / "identity.json", identity)
    _json(root / "runtime.json", provenance); _json(root / "result.json", payload)
    _manifest(root, children=children, expected_counts=expected_counts)


def _fold_identity(parent: Mapping[str, Any], number: int, fold: Fold,
                   selected_candidate_id: str, test_identity: Mapping[str, Any],
                   cost_analysis: Mapping[str, Any]) -> dict:
    payload = {"fold_schema_version": "2.0-fold-1", "parent_experiment_id": parent["experiment_id"],
        "fold_number": number, "boundaries": {k: [str(x) for x in getattr(fold, k)]
        for k in ("train", "validation", "warmup", "test")},
        "selected_candidate_id": selected_candidate_id,
        "test_child_experiment_id": test_identity["experiment_id"],
        "cost_analysis": dict(cost_analysis)}
    return {"fold_id": hashlib.sha256(canonical_json(payload).encode()).hexdigest(),
            "canonical_inputs": payload}


def _cost_results(config: ExperimentConfig, parameters: Mapping[str, Any], fold: Fold,
                  base_bundle: Path, root: Path) -> dict:
    """Run post-selection stresses without ever re-entering candidate selection."""
    output = {"closed_loop": {}, "fixed_path": {}}
    for name, costs in config.cost_analysis.get("closed_loop", {}).items():
        stressed = replace(config,
            fee_parameters={**config.fee_parameters, **{k:v for k,v in costs.items() if k != "slippage_rate"}},
            slippage_parameters={**config.slippage_parameters, **{k:v for k,v in costs.items() if k == "slippage_rate"}})
        bundle = _child(stressed, parameters, min((*fold.warmup,*fold.test)), max(fold.test),
                        min(fold.test), root/"closed_loop"/name)
        output["closed_loop"][name] = {"label":"closed-loop execution rerun", "metrics":_metrics(bundle,fold.test),
                                               "bundle":str(bundle.relative_to(root))}
    if config.cost_analysis.get("fixed_path"):
        with (base_bundle/"fills.csv").open(newline="",encoding="utf-8") as stream:
            fills_rows=[r for r in csv.DictReader(stream) if r["date"] in {str(x) for x in fold.test}]
        base_slip=float(config.slippage_parameters.get("slippage_rate",0.0))
        fills=[ReplayFill(r["symbol"],r["side"],int(r["quantity"]),
                          float(r["price"])/(1+(base_slip if r["side"]=="buy" else -base_slip))) for r in fills_rows]
        with (base_bundle/"marks.csv").open(newline="",encoding="utf-8") as stream:
            marks_rows=[r for r in csv.DictReader(stream) if r["date"]==str(max(fold.test)) and r["available"]=="True"]
        marks={r["symbol"]:float(r["close"]) for r in marks_rows}
        fill_path_hash = hashlib.sha256(canonical_json(fills_rows).encode()).hexdigest()
        base_equity = fixed_path_cost_replay(config.initial_cash, fills,
            commission_rate=float(config.fee_parameters.get("commission_rate", 0)),
            minimum_commission=float(config.fee_parameters.get("minimum_commission", 0)),
            slippage_rate=base_slip, final_prices=marks)
        with (base_bundle/"equity_curve.csv").open(newline="",encoding="utf-8") as stream:
            final_equity = next(float(r["equity"]) for r in csv.DictReader(stream)
                                if r["date"] == str(max(fold.test)))
        if abs(base_equity - final_equity) > 1e-6:
            raise ValueError("base fixed-path replay does not reconcile with frozen-test final equity")
        output["fixed_path_contract"] = {"base_fill_path_sha256": fill_path_hash,
            "starting_cash": config.initial_cash, "starting_positions": {},
            "base_replay_final_equity": base_equity, "reconciliation_tolerance": 1e-6}
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
    require_no_fit_strategy(config.strategy_class)
    started = time.monotonic(); manifest = SnapshotManifest.load(config.data_manifest)
    manifest.validate(config.data_root, expected_symbols=config.universe, allow_mixed_adjustments=config.allow_mixed_adjustments)
    parent, provenance = _parent(config, manifest); root = Path(config.output_directory) / parent["experiment_id"]
    if (root / "artifact_manifest.json").exists(): validate_artifact_directory(root); return root
    root.mkdir(parents=True, exist_ok=True); fold = _fixed_fold(config, _calendar(config, manifest))
    rows, selected = _evaluate(config, manifest, parent, provenance, fold, root, started)
    # This is the sole frozen-test invocation.
    test_bundle = _child(config, selected["parameters"], min((*fold.warmup, *fold.test)), max(fold.test),
                         min(fold.test), root / "frozen_test")
    test_identity = json.loads((test_bundle / "identity.json").read_text(encoding="utf-8"))
    costs = _cost_results(config,selected["parameters"],fold,test_bundle,root)
    payload = {"mode":"fixed_selection", "boundaries": {k:[str(x) for x in getattr(fold,k)] for k in ("train","validation","warmup","test")},
               "candidate_table":rows, "selected_parameters":selected["parameters"],
               "selected_candidate_id": selected["candidate_id"],
               "test_child_experiment_id": test_identity["experiment_id"],
               "fit_applicable": False, "fit_reason":"registered fixed-rule strategy; train boundary is unused",
               "warmup_policy": config.warmup_policy,
               "validation_metrics":selected["validation_metrics"], "test_metrics":_metrics(test_bundle, fold.test),
               "frozen_test_bundle":str(test_bundle.relative_to(root)),
               "cost_analysis":costs,
               "selection_rationale":{"objective":config.parameter_selection["selection_objective"],"direction":config.parameter_selection["objective_direction"],"tie_break_rules":config.parameter_selection["tie_break_rules"]}}
    children = {str((root/"candidates"/r["candidate_id"]).relative_to(root)):root/"candidates"/r["candidate_id"] for r in rows}
    children[str(test_bundle.relative_to(root))] = test_bundle
    for result in costs.get("closed_loop", {}).values(): children[result["bundle"]] = root/result["bundle"]
    _write_parent(root, config, parent, provenance, payload, children=children,
                  expected_counts={"candidates":len(rows),"folds":0,"frozen_tests":1,
                                   "cost_scenarios":len(costs.get("closed_loop",{}))})
    return root


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
    require_no_fit_strategy(config.strategy_class)
    started=time.monotonic(); manifest=SnapshotManifest.load(config.data_manifest)
    manifest.validate(config.data_root, expected_symbols=config.universe, allow_mixed_adjustments=config.allow_mixed_adjustments)
    parent,provenance=_parent(config,manifest); root=Path(config.output_directory)/parent["experiment_id"]
    if (root/"artifact_manifest.json").exists(): validate_artifact_directory(root); return root
    root.mkdir(parents=True,exist_ok=True); reports=[]; local_paths=[]; fold_state=[]
    for number,fold in enumerate(_folds(config,_calendar(config,manifest))):
        fold_root=root/f"fold-{number:04d}"; fold_root.mkdir(exist_ok=True)
        rows,selected=_evaluate(config,manifest,parent,provenance,fold,fold_root,started)
        bundle=_child(config,selected["parameters"],min((*fold.warmup,*fold.test)),max(fold.test),
                      min(fold.test),fold_root/"frozen_test")
        metrics=_metrics(bundle,fold.test)
        local = _equity_rows(bundle, fold.test); local_paths.append(local)
        _write_equity(fold_root/"fold_local_equity.csv", local)
        test_identity=json.loads((bundle/"identity.json").read_text(encoding="utf-8"))
        costs=_cost_results(config,selected["parameters"],fold,bundle,fold_root)
        fold_identity=_fold_identity(parent,number,fold,selected["candidate_id"],test_identity,config.cost_analysis)
        report={"fold":number,"boundaries":{k:[str(x) for x in getattr(fold,k)] for k in ("train","validation","warmup","test")},
                "candidate_table":rows,"selected_parameters":selected["parameters"],"validation_metrics":selected["validation_metrics"],
                "selected_candidate_id":selected["candidate_id"], "fold_id":fold_identity["fold_id"],
                "test_child_experiment_id":test_identity["experiment_id"],
                "fit_applicable":False,"fit_reason":"registered fixed-rule strategy; train boundary is unused",
                "warmup_policy":config.warmup_policy,
                "test_metrics":metrics,"frozen_test_bundle":str(bundle.relative_to(fold_root)),
                "cost_analysis":costs}
        reports.append(report); fold_state.append((fold_root,rows,bundle,costs,fold_identity,local))
    stitched=list(stitch_oos_equity(local_paths,config.initial_cash))
    _write_equity(root/"stitched_oos_equity.csv",stitched)
    by_date={row["date"]:row for row in stitched}
    for report,(fold_root,rows,bundle,costs,fold_identity,local) in zip(reports,fold_state):
        rebased=[by_date[row["date"]] for row in local]
        _write_equity(fold_root/"fold_rebased_equity.csv",rebased)
        report["fold_local_equity_artifact"]="fold_local_equity.csv"
        report["rebased_stitched_equity_artifact"]="fold_rebased_equity.csv"
        _remove_undeclared_direct_files(fold_root,{"fold_local_equity.csv","fold_rebased_equity.csv",
            "fold_result.json","configuration.json","identity.json","artifact_manifest.json"})
        _json(fold_root/"fold_result.json",report); _json(fold_root/"configuration.json",asdict(config))
        _json(fold_root/"identity.json",fold_identity)
        children={str((fold_root/"candidates"/r["candidate_id"]).relative_to(fold_root)):fold_root/"candidates"/r["candidate_id"] for r in rows}
        children[str(bundle.relative_to(fold_root))]=bundle
        for result in costs.get("closed_loop",{}).values(): children[result["bundle"]]=fold_root/result["bundle"]
        _manifest(fold_root,children=children,expected_counts={"candidates":len(rows),"frozen_tests":1,
                   "cost_scenarios":len(costs.get("closed_loop",{}))})
    vals=[float(x["equity"]) for x in stitched]; peak=0.; draw=[]
    for row in stitched: peak=max(peak,float(row["equity"])); draw.append({"date":row["date"],"drawdown":float(row["equity"])/peak-1})
    with (root/"stitched_oos_drawdown.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=["date","drawdown"]); w.writeheader(); w.writerows(draw)
    payload={"mode":"walk_forward","folds":reports,"parameter_switch_history":[{"fold":r["fold"],"parameters":r["selected_parameters"]} for r in reports],"stitched_metrics":performance_metrics(vals)}
    children={str(state[0].relative_to(root)):state[0] for state in fold_state}
    _write_parent(root,config,parent,provenance,payload,children=children,
                  expected_counts={"folds":len(reports)})
    return root
