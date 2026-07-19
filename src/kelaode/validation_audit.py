"""Independent, read-only verification of sealed schema-2.0 selection artifacts."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from .cost_analysis import ReplayFill, fixed_path_cost_replay
from .experiment_metrics import benchmark_metrics, performance_metrics
from .selection_runner import stitch_oos_equity, validate_artifact_directory
from .snapshot import SnapshotManifest, canonical_json

_TOLERANCE = 1e-6
_DEFAULT_POLICY = Path(__file__).parents[2] / "configs/validation/sit_validation_policy.json"


def _rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise ValueError(f"required audit artifact is absent: {path}")
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _json(path: Path) -> Any:
    if not path.is_file():
        raise ValueError(f"required audit artifact is absent: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _identity(value: Mapping[str, Any], id_field: str, label: str) -> None:
    if set(value) != {id_field, "canonical_inputs"}:
        raise ValueError(f"{label} identity contract is malformed")
    expected = hashlib.sha256(canonical_json(value["canonical_inputs"]).encode()).hexdigest()
    if value[id_field] != expected:
        raise ValueError(f"{label} identity mismatch")


def _close(actual: float, expected: float, label: str, tolerance: float = _TOLERANCE) -> None:
    if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=tolerance):
        raise ValueError(f"{label} mismatch: actual={actual}, expected={expected}")


def _audit_run(bundle: Path, official_listing: Mapping[str, str], test_dates: Sequence[str],
               expected_parameters: Mapping[str, Any], expected_test_id: str) -> list[str]:
    checks: list[str] = []
    identity = _json(bundle / "identity.json")
    _identity(identity, "experiment_id", "test child")
    if identity["experiment_id"] != expected_test_id:
        raise ValueError("test-child identity is not associated with selected result")
    child_config = _json(bundle / "configuration.json")
    if identity["canonical_inputs"].get("configuration") != child_config:
        raise ValueError("test-child configuration differs from canonical identity")
    if child_config["strategy_parameters"] != dict(expected_parameters):
        raise ValueError("test-child parameters differ from selected parameters")
    checks.extend(("test_child_identity", "test_child_selected_parameter_association"))

    equity_rows, cash_rows = _rows(bundle / "equity_curve.csv"), _rows(bundle / "cash.csv")
    equity = {r["date"]: float(r["equity"]) for r in equity_rows}
    cash = {r["date"]: float(r["cash"]) for r in cash_rows}
    positions = {(r["date"], r["symbol"]): int(r["quantity"])
                 for r in _rows(bundle / "positions.csv")}
    marks = {(r["date"], r["symbol"]): (float(r["close"]) if r["close"] else None)
             for r in _rows(bundle / "marks.csv")}
    for day, value in equity.items():
        reconstructed = cash[day] + sum(q * (marks[(day, symbol)] or 0.0)
                                        for (date_, symbol), q in positions.items() if date_ == day)
        _close(reconstructed, value, f"equity accounting on {day}")
    checks.append("equity_equals_cash_plus_marked_positions")

    fills_rows, trades_rows = _rows(bundle / "fills.csv"), _rows(bundle / "trades.csv")
    fill_keys = ("date", "symbol", "side")
    if len(fills_rows) != len(trades_rows):
        raise ValueError("trades do not reconcile with fills")
    for fill, trade in zip(fills_rows, trades_rows):
        if (tuple(fill[k] for k in fill_keys) != tuple(trade[k] for k in fill_keys) or
                int(fill["quantity"]) != int(trade["quantity"])):
            raise ValueError("trades do not reconcile with fills")
        for field in ("price", "commission"):
            _close(float(fill[field]), float(trade[field]), f"trade/fill {field}")
    checks.append("trades_reconcile_with_fills")

    execution_start = child_config.get("execution_start_date")
    if not execution_start:
        raise ValueError("test child has no execution boundary")
    orders = _rows(bundle / "orders.csv")
    if any(r["date"] < execution_start or r["signal_date"] >= r["date"] for r in orders):
        raise ValueError("warm-up or non-next-open order detected")
    if any(r["date"] < execution_start for r in fills_rows):
        raise ValueError("warm-up fill detected")
    if any(q and day < execution_start for (day, _), q in positions.items()):
        raise ValueError("warm-up position detected")
    checks.extend(("no_warmup_orders", "no_warmup_fills", "no_warmup_positions", "signal_precedes_execution"))

    if set(official_listing) != set(child_config["universe"]):
        raise ValueError("official listing metadata does not exactly cover the universe")
    for (day, symbol), quantity in positions.items():
        if quantity and day < official_listing[symbol]:
            raise ValueError(f"official pre-listing position detected: {symbol} {day}")
    checks.append("official_listing_dates_and_no_prelisting_positions")

    strategy_dates = list(equity)
    benchmark_rows = _rows(bundle / "benchmark_curve.csv")
    benchmark_dates = [r["date"] for r in benchmark_rows]
    if benchmark_dates != strategy_dates:
        raise ValueError("benchmark dates are not exactly aligned")
    saved_benchmark = _json(bundle / "benchmark_metrics.json")
    reconstructed_benchmark = benchmark_metrics(
        equity, {r["date"]: float(r["equity"]) for r in benchmark_rows})
    for name, value in reconstructed_benchmark.items():
        _close(float(saved_benchmark[name]), float(value), f"benchmark metric {name}", 1e-10)
    benchmark_values = [float(r["equity"]) for r in benchmark_rows]
    benchmark_performance = performance_metrics(benchmark_values)
    for name in ("total_return", "annualized_volatility"):
        _close(float(saved_benchmark[f"benchmark_{name}"]), float(benchmark_performance[name]),
               f"benchmark standalone metric {name}", 1e-10)
    if child_config["benchmark_definitions"].get("type") == "equal_weight_buy_and_hold":
        execution_start = child_config.get("execution_start_date")
        benchmark_cash = {r["date"]: float(r["cash"]) for r in _rows(bundle / "benchmark_cash.csv")}
        benchmark_positions = {(r["date"], r["symbol"]): int(r["quantity"])
                               for r in _rows(bundle / "benchmark_positions.csv")}
        benchmark_marks = {(r["date"], r["symbol"]): (float(r["close"]) if r["close"] else None)
                           for r in _rows(bundle / "benchmark_marks.csv")}
        benchmark_orders, benchmark_fills = (_rows(bundle / "benchmark_orders.csv"),
                                              _rows(bundle / "benchmark_fills.csv"))
        if not benchmark_orders or not benchmark_fills or not any(benchmark_positions.values()):
            raise ValueError("equal-weight buy-and-hold benchmark has no orders, fills, or positions")
        if len(set(benchmark_values)) == 1 or benchmark_performance["annualized_volatility"] == 0:
            raise ValueError("equal-weight buy-and-hold benchmark is a zero-volatility cash curve")
        if any(r["date"] < execution_start for r in benchmark_orders + benchmark_fills):
            raise ValueError("benchmark warm-up order or fill detected")
        if any(quantity and day < execution_start for (day, _), quantity in benchmark_positions.items()):
            raise ValueError("benchmark warm-up position detected")
        first_signal = min(r["signal_date"] for r in benchmark_orders)
        first_execution = min(r["date"] for r in benchmark_orders)
        if first_signal < execution_start or first_execution <= first_signal:
            raise ValueError("benchmark signal/execution boundary is invalid")
        target_rows = [r for r in _rows(bundle / "benchmark_weights.csv")
                       if r["date"] == first_signal and float(r["target_weight"]) > 0]
        expected_symbols = child_config["benchmark_definitions"]["symbols"]
        if ({r["symbol"] for r in target_rows} != set(expected_symbols) or
                any(not math.isclose(float(r["target_weight"]), 1 / len(expected_symbols),
                                     rel_tol=0, abs_tol=1e-12) for r in target_rows)):
            raise ValueError("benchmark initial target weights are not equal across configured symbols")
        for day, value in zip(benchmark_dates, benchmark_values):
            reconstructed = benchmark_cash[day] + sum(
                quantity * (benchmark_marks[(day, symbol)] or 0.0)
                for (position_day, symbol), quantity in benchmark_positions.items() if position_day == day)
            _close(reconstructed, value, f"benchmark accounting on {day}")
        checks.extend(("benchmark_nonconstant_invested_curve", "benchmark_orders_fills_positions",
                       "no_benchmark_warmup_activity", "benchmark_signal_execution_boundary",
                       "benchmark_equal_initial_targets", "benchmark_accounting_reconstruction"))
    checks.extend(("benchmark_date_alignment", "benchmark_total_return_reconstruction",
                   "benchmark_metrics_reconstruction", "strategy_excess_return_reconstruction"))

    wanted = set(test_dates)
    test_equity = [float(r["equity"]) for r in equity_rows if r["date"] in wanted]
    test_trades = [{**r, "notional": float(r["price"]) * int(r["quantity"])}
                   for r in trades_rows if r["date"] in wanted]
    turnover = performance_metrics(test_equity, test_trades)["turnover"]
    return checks + [f"reconstructed_turnover={turnover:.17g}"]


def _audit_costs(costs: Mapping[str, Any], config: Mapping[str, Any], bundle: Path,
                 test_dates: Sequence[str], expected_turnover: float) -> list[str]:
    required = {"closed_loop", "fixed_path", "fixed_path_contract"}
    if set(costs) != required:
        raise ValueError("closed-loop and fixed-path results must be present and separately labeled")
    if any(v.get("label") != "closed-loop execution rerun" for v in costs["closed_loop"].values()):
        raise ValueError("closed-loop result label mismatch")
    if any(v.get("label") != "fixed frozen fill-path repricing" for v in costs["fixed_path"].values()):
        raise ValueError("fixed-path result label mismatch")
    contract = costs["fixed_path_contract"]
    if contract.get("starting_cash") != config["initial_cash"] or contract.get("starting_positions") != {}:
        raise ValueError("fixed-path starting cash/positions mismatch")
    fills_rows = [r for r in _rows(bundle / "fills.csv") if r["date"] in set(test_dates)]
    digest = hashlib.sha256(canonical_json(fills_rows).encode()).hexdigest()
    if contract.get("base_fill_path_sha256") != digest:
        raise ValueError("fixed-path fill SHA-256 mismatch")
    base_slip = float(config["slippage_parameters"].get("slippage_rate", 0))
    fills = [ReplayFill(r["symbol"], r["side"], int(r["quantity"]),
                        float(r["price"]) / (1 + (base_slip if r["side"] == "buy" else -base_slip)))
             for r in fills_rows]
    final_day = max(test_dates)
    marks = {r["symbol"]: float(r["close"]) for r in _rows(bundle / "marks.csv")
             if r["date"] == final_day and r["available"] == "True"}
    base = fixed_path_cost_replay(float(contract["starting_cash"]), fills,
        commission_rate=float(config["fee_parameters"].get("commission_rate", 0)),
        minimum_commission=float(config["fee_parameters"].get("minimum_commission", 0)),
        slippage_rate=base_slip, final_prices=marks)
    _close(base, float(contract["base_replay_final_equity"]), "fixed-path base replay")
    final_equity = next(float(r["equity"]) for r in _rows(bundle / "equity_curve.csv") if r["date"] == final_day)
    _close(base, final_equity, "fixed-path base/original reconciliation")
    scenarios = config["cost_analysis"]["fixed_path"]
    for name, value in costs["fixed_path"].items():
        scenario = scenarios[name]
        replay = fixed_path_cost_replay(float(contract["starting_cash"]), fills,
            commission_rate=float(scenario.get("commission_rate", config["fee_parameters"].get("commission_rate", 0))),
            minimum_commission=float(scenario.get("minimum_commission", config["fee_parameters"].get("minimum_commission", 0))),
            slippage_rate=float(scenario.get("slippage_rate", base_slip)), final_prices=marks)
        _close(replay, float(value["final_equity"]), f"fixed-path scenario {name}")
        if all(float(scenario.get(k, 0)) >= float({"commission_rate": config["fee_parameters"].get("commission_rate", 0),
               "minimum_commission": config["fee_parameters"].get("minimum_commission", 0),
               "slippage_rate": base_slip}[k]) for k in ("commission_rate", "minimum_commission", "slippage_rate")) and replay > base + _TOLERANCE:
            raise ValueError("higher fixed-path costs improved final equity")
    return ["cost_modes_separately_labeled", "fixed_path_fill_hash", "fixed_path_starting_state",
            "fixed_path_base_reconciliation", "fixed_path_scenario_reconstruction", "fixed_path_cost_monotonicity"]


def audit_selection(root: str | Path, policy_path: str | Path = _DEFAULT_POLICY) -> dict:
    """Audit immutable results without importing a strategy or rerunning selection."""
    root, checks = Path(root), []
    validate_artifact_directory(root)
    checks.append("artifact_hashes")
    config, parent, result = _json(root / "configuration.json"), _json(root / "identity.json"), _json(root / "result.json")
    _identity(parent, "experiment_id", "parent")
    if parent["canonical_inputs"].get("configuration") != config:
        raise ValueError("parent configuration differs from canonical identity")
    checks.append("parent_experiment_identity")
    manifest = SnapshotManifest.load(config["data_manifest"])
    manifest.validate(config["data_root"], expected_symbols=config["universe"], allow_mixed_adjustments=False)
    if parent["canonical_inputs"].get("manifest_hash") != manifest.hash or parent["canonical_inputs"].get("input_hashes") != [e.sha256 for e in manifest.entries]:
        raise ValueError("parent data identity mismatch")
    checks.extend(("data_hashes", "consistent_adjustment", "parent_data_identity"))
    policy = _json(Path(policy_path))
    official = policy.get("official_listing_dates")
    if not isinstance(official, dict):
        raise ValueError("immutable protocol metadata has no official listing dates")
    checks.append("immutable_official_listing_metadata")

    bundles: list[tuple[Path, Mapping[str, Any], Sequence[str], str, Mapping[str, Any]]] = []
    if result["mode"] == "fixed_selection":
        candidate_root = root / "candidates"
        candidate_dirs = [p for p in candidate_root.iterdir() if p.is_dir()]
        if len(candidate_dirs) != len(result["candidate_table"]):
            raise ValueError("candidate directory count mismatch")
        for row in result["candidate_table"]:
            identity = _json(candidate_root / row["candidate_id"] / "identity.json")
            _identity(identity, "candidate_id", "candidate")
            if identity["candidate_id"] != row["candidate_id"] or identity["canonical_inputs"].get("parent_experiment_id") != parent["experiment_id"]:
                raise ValueError("candidate identity association mismatch")
            if any((candidate_root / row["candidate_id"]).glob("**/frozen_test")):
                raise ValueError("unselected candidate has a frozen-test bundle")
        selected = next((r for r in result["candidate_table"] if r["candidate_id"] == result["selected_candidate_id"]), None)
        if selected is None or selected["parameters"] != result["selected_parameters"]:
            raise ValueError("selected candidate membership mismatch")
        test_roots = [p for p in root.glob("frozen_test/*") if (p / "artifact_manifest.json").is_file()]
        if len(test_roots) != 1 or test_roots[0] != root / result["frozen_test_bundle"]:
            raise ValueError("fixed selection must contain exactly one selected frozen-test bundle")
        bundles.append((test_roots[0], result["selected_parameters"], result["boundaries"]["test"],
                        result["test_child_experiment_id"], result["cost_analysis"]))
        checks.extend(("candidate_identities", "selected_candidate_membership", "exactly_one_selected_frozen_test", "no_unselected_test_bundle"))
    elif result["mode"] == "walk_forward":
        seen: set[str] = set(); local_paths = []
        for fold in result["folds"]:
            fold_root = root / f"fold-{fold['fold']:04d}"
            fold_identity = _json(fold_root / "identity.json")
            _identity(fold_identity, "fold_id", "fold")
            if fold_identity["fold_id"] != fold["fold_id"] or fold_identity["canonical_inputs"].get("parent_experiment_id") != parent["experiment_id"]:
                raise ValueError("fold identity association mismatch")
            for row in fold["candidate_table"]:
                candidate_root = fold_root / "candidates" / row["candidate_id"]
                candidate_identity = _json(candidate_root / "identity.json")
                _identity(candidate_identity, "candidate_id", "candidate")
                if candidate_identity["candidate_id"] != row["candidate_id"] or candidate_identity["canonical_inputs"].get("parent_experiment_id") != parent["experiment_id"]:
                    raise ValueError("fold candidate identity mismatch")
                if any(candidate_root.glob("**/frozen_test")):
                    raise ValueError("unselected fold candidate has a frozen-test bundle")
            selected = next((r for r in fold["candidate_table"] if r["candidate_id"] == fold["selected_candidate_id"]), None)
            if selected is None or selected["parameters"] != fold["selected_parameters"]:
                raise ValueError("fold selected candidate membership mismatch")
            dates = fold["boundaries"]["test"]
            if seen.intersection(dates):
                raise ValueError("overlapping OOS dates")
            seen.update(dates)
            test_roots = [p for p in fold_root.glob("frozen_test/*") if (p / "artifact_manifest.json").is_file()]
            expected = fold_root / fold["frozen_test_bundle"]
            if len(test_roots) != 1 or test_roots[0] != expected:
                raise ValueError("walk-forward fold must contain exactly one selected frozen-test bundle")
            bundles.append((expected, fold["selected_parameters"], dates, fold["test_child_experiment_id"], fold["cost_analysis"]))
            local_paths.append([{"date": r["date"], "equity": float(r["equity"])} for r in _rows(fold_root / "fold_local_equity.csv")])
        reconstructed = list(stitch_oos_equity(local_paths, float(config["initial_cash"])))
        saved = [{"date": r["date"], "equity": float(r["equity"])} for r in _rows(root / "stitched_oos_equity.csv")]
        if len(saved) != len(reconstructed):
            raise ValueError("stitched OOS length mismatch")
        for actual, expected in zip(saved, reconstructed):
            if actual["date"] != expected["date"]:
                raise ValueError("stitched OOS date mismatch")
            _close(actual["equity"], expected["equity"], f"stitched OOS equity {actual['date']}")
        peak, reconstructed_dd = 0.0, []
        for row in reconstructed:
            peak = max(peak, row["equity"]); reconstructed_dd.append(row["equity"] / peak - 1)
        saved_dd = _rows(root / "stitched_oos_drawdown.csv")
        if len(saved_dd) != len(reconstructed_dd):
            raise ValueError("stitched drawdown length mismatch")
        for row, value in zip(saved_dd, reconstructed_dd):
            _close(float(row["drawdown"]), value, f"stitched drawdown {row['date']}")
        metrics = performance_metrics([r["equity"] for r in reconstructed])
        for name, value in metrics.items():
            _close(float(result["stitched_metrics"][name]), float(value), f"stitched metric {name}", 1e-10)
        checks.extend(("fold_identities", "candidate_identities", "selected_candidate_membership",
                       "exactly_one_selected_test_per_fold", "no_unselected_test_bundle", "no_oos_overlap",
                       "stitched_oos_equity_reconstruction", "stitched_drawdown_reconstruction", "stitched_metrics_reconstruction"))
    else:
        raise ValueError("unsupported selection result mode")

    for bundle, parameters, dates, test_id, costs in bundles:
        run_checks = _audit_run(bundle, official, dates, {**config["strategy_parameters"], **parameters}, test_id)
        turnover = float(next(x.split("=", 1)[1] for x in run_checks if x.startswith("reconstructed_turnover=")))
        expected_metrics = result["test_metrics"] if result["mode"] == "fixed_selection" else next(f["test_metrics"] for f in result["folds"] if f["test_child_experiment_id"] == test_id)
        _close(turnover, float(expected_metrics["turnover"]), "test-period turnover", 1e-10)
        checks.extend(x for x in run_checks if not x.startswith("reconstructed_turnover="))
        checks.append("test_period_turnover_reconstruction")
        checks.extend(_audit_costs(costs, config, bundle, dates, turnover))
    return {"status": "pass", "mode": result["mode"], "bundle_count": len(bundles), "checks": sorted(set(checks))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m kelaode.validation_audit")
    parser.add_argument("--artifacts", required=True)
    parser.add_argument("--policy", default=str(_DEFAULT_POLICY))
    args = parser.parse_args(argv)
    print(json.dumps(audit_selection(args.artifacts, args.policy), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
