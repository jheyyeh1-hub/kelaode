"""Configuration-only shared experiment runner and immutable result bundles."""
from __future__ import annotations

import csv
import json
import os
import random
import shutil
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Iterable, Mapping

from .experiment import ExperimentConfig, experiment_identity, experiment_metadata
from .experiment_metrics import benchmark_metrics, execution_statistics, performance_metrics
from .market_data import DailyBar, read_daily_bars
from .portfolio import EqualWeightBuyAndHold, PortfolioBacktestConfig, PortfolioBacktester
from .strategy_registry import create_strategy
from .execution import ExcessVolumePolicy
from .snapshot import SnapshotManifest, canonical_json, sha256_file

BUNDLE_SCHEMA_VERSION = "1.1"
ARTIFACT_COLUMNS = {
    "equity_curve.csv": ("date", "equity"), "cash.csv": ("date", "cash"),
    "positions.csv": ("date", "symbol", "quantity"),
    "marks.csv": ("date", "symbol", "close", "available"),
    "weights.csv": ("date", "symbol", "target_weight"),
    "generated_orders.csv": ("date", "order_id", "symbol", "side", "quantity", "limit_price"),
    "orders.csv": ("date", "signal_date", "symbol", "side", "requested_quantity", "filled_quantity", "status", "reason"),
    "validated_orders.csv": ("date", "order_id", "symbol", "side", "quantity", "limit_price", "estimated_cost"),
    "fills.csv": ("date", "order_id", "symbol", "side", "quantity", "price", "commission", "stamp_duty"),
    "rejections.csv": ("date", "order_id", "symbol", "reason", "reason_code"),
    "trades.csv": ("date", "symbol", "side", "quantity", "price", "commission"),
    "exposure.csv": ("date", "gross_exposure", "net_exposure"),
    "turnover.csv": ("turnover",), "drawdown.csv": ("date", "drawdown"),
    "benchmark_curve.csv": ("date", "equity"),
    "benchmark_cash.csv": ("date", "cash"),
    "benchmark_positions.csv": ("date", "symbol", "quantity"),
    "benchmark_marks.csv": ("date", "symbol", "close", "available"),
    "benchmark_weights.csv": ("date", "symbol", "target_weight"),
    "benchmark_orders.csv": ("date", "signal_date", "symbol", "side", "requested_quantity", "filled_quantity", "status", "reason"),
    "benchmark_fills.csv": ("date", "order_id", "symbol", "side", "quantity", "price", "commission", "stamp_duty"),
    "parameter_results.csv": ("applicable", "parameters", "validation_metrics", "error"),
}
JSON_ARTIFACTS = ("configuration.json", "identity.json", "data_manifest.json", "metrics.json",
                  "benchmark_metrics.json", "split_definitions.json", "selected_parameters.json",
                  "fold_results.json", "daily_audits.json", "runtime.json", "contracts.json",
                  "resolved_benchmark.json")

def _write_csv(path: Path, rows: Iterable[Mapping]) -> None:
    columns = ARTIFACT_COLUMNS[path.name]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")

def _validate_cache(root: Path, identity: Mapping) -> None:
    try:
        saved_identity = json.loads((root / "identity.json").read_text(encoding="utf-8"))
        bundle = json.loads((root / "artifact_manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("cached result bundle is incomplete or unreadable") from exc
    if canonical_json(saved_identity) != canonical_json(identity):
        raise ValueError("cached result identity does not exactly match")
    expected = set(ARTIFACT_COLUMNS) | set(JSON_ARTIFACTS)
    if bundle.get("schema_version") != BUNDLE_SCHEMA_VERSION or set(bundle.get("artifacts", {})) != expected:
        raise ValueError("cached result bundle contract is incomplete")
    for name, expected_hash in bundle["artifacts"].items():
        path = root / name
        if not path.is_file() or sha256_file(path) != expected_hash:
            raise ValueError(f"cached result artifact failed integrity validation: {name}")

def _aligned_data(config: ExperimentConfig, manifest: SnapshotManifest) -> dict[str, list[DailyBar]]:
    start, end = date.fromisoformat(config.start_date), date.fromisoformat(config.end_date)
    data = {entry.symbol: [bar for bar in read_daily_bars(Path(config.data_root) / entry.relative_path)
                           if start <= bar.trade_date <= end] for entry in manifest.entries}
    if any(not bars for bars in data.values()):
        raise ValueError("configured date range leaves an input symbol empty")
    if config.data_alignment_mode == "intersection":
        calendar = set.intersection(*({bar.trade_date for bar in bars} for bars in data.values()))
        if not calendar:
            raise ValueError("intersection data alignment produced an empty calendar")
        data = {symbol: [bar for bar in bars if bar.trade_date in calendar] for symbol, bars in data.items()}
    return data

def _engine_config(config: ExperimentConfig) -> PortfolioBacktestConfig:
    merged = {**config.constraint_parameters, **config.execution_parameters,
              **config.fee_parameters, **config.slippage_parameters, "initial_cash": config.initial_cash}
    merged.pop("execution_timing")
    if "partial_fill_policy" in merged:
        try:
            merged["partial_fill_policy"] = ExcessVolumePolicy(merged["partial_fill_policy"])
        except ValueError as exc:
            raise ValueError("invalid execution partial_fill_policy") from exc
    unknown = set(merged) - set(PortfolioBacktestConfig.__dataclass_fields__)
    if unknown:
        raise ValueError(f"unsupported backtest configuration fields: {sorted(unknown)}")
    return PortfolioBacktestConfig(**merged)

def _strategy(config: ExperimentConfig):
    return create_strategy(config.strategy_class, config.universe, config.strategy_parameters)

def run_experiment(config: ExperimentConfig) -> Path:
    """Validate, execute, and atomically publish one daily no-fit experiment."""
    if config.experiment_mode != "run":
        raise ValueError("run_experiment requires experiment_mode=run")
    if config.split_definitions["type"] != "none":
        raise ValueError("run supports only no-fit experiments; use the explicit validation/walk-forward API for splits")
    started = time.monotonic()
    manifest = SnapshotManifest.load(config.data_manifest)
    manifest.validate(config.data_root, expected_symbols=config.universe,
                      allow_mixed_adjustments=config.allow_mixed_adjustments)
    metadata = experiment_metadata(config, config.data_manifest)
    identity = experiment_identity(config, manifest, metadata)
    root = Path(config.output_directory) / identity["experiment_id"]
    if root.exists():
        _validate_cache(root, identity)
        return root

    data = _aligned_data(config, manifest)
    engine_config = _engine_config(config)
    random.seed(config.random_seed)
    execution_start = date.fromisoformat(config.execution_start_date) if config.execution_start_date else None
    result = PortfolioBacktester(engine_config).run(data, _strategy(config), execution_start=execution_start)
    benchmark_type = config.benchmark_definitions["type"]
    benchmark_symbols = (() if benchmark_type == "none" else
        tuple(config.benchmark_definitions["symbols"]) if benchmark_type == "equal_weight_buy_and_hold" else
        (config.benchmark_definitions["symbol"],))
    benchmark = (PortfolioBacktester(engine_config).run(
        {symbol: data[symbol] for symbol in benchmark_symbols},
        EqualWeightBuyAndHold(benchmark_symbols, execution_start=execution_start),
        execution_start=execution_start)
        if benchmark_symbols else None)
    if benchmark and tuple(result.equity_curve) != tuple(benchmark.equity_curve):
        raise ValueError("benchmark and strategy dates are not exactly aligned")

    tmp = root.with_name(f".{root.name}.{uuid.uuid4().hex}.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True)
    try:
        _write_json(tmp / "configuration.json", json.loads(config.to_json()))
        _write_json(tmp / "identity.json", identity)
        _write_json(tmp / "data_manifest.json", manifest.as_dict())
        trade_rows = [{"date": trade.trade_date, "symbol": trade.symbol, "side": trade.side, "quantity": trade.quantity,
                       "price": trade.price, "commission": trade.commission}
                      for trade in result.trades]
        metrics = performance_metrics(list(result.equity_curve.values()))
        metrics.update(execution_statistics(trade_rows, config.initial_cash))
        metrics.update({"order_count": len(result.orders),
                        "rejected_order_count": len(result.rejections),
                        "gross_exposure": sum(sum(abs(x) for x in weights.values())
                                              for weights in result.weights_by_date.values()) / len(result.weights_by_date),
                        "net_exposure": sum(sum(weights.values())
                                            for weights in result.weights_by_date.values()) / len(result.weights_by_date)})
        if abs(metrics["turnover"] - result.turnover) > 1e-12:
            raise ValueError("primary metrics turnover does not reconcile with backtest result")
        _write_json(tmp / "metrics.json", metrics)
        if benchmark:
            benchmark_report = benchmark_metrics(result.equity_curve, benchmark.equity_curve)
            standalone = performance_metrics(list(benchmark.equity_curve.values()))
            benchmark_report.update({f"benchmark_{name}": standalone[name]
                                     for name in ("total_return", "annualized_volatility")})
        else:
            benchmark_report = {"applicable": False, "reason": "benchmark type is none"}
        _write_json(tmp / "benchmark_metrics.json", benchmark_report)
        _write_json(tmp / "resolved_benchmark.json", config.benchmark_definitions)
        _write_json(tmp / "split_definitions.json", config.split_definitions)
        _write_json(tmp / "selected_parameters.json", {"applicable": False, "reason": "no parameter selection configured"})
        _write_json(tmp / "fold_results.json", {"applicable": False, "folds": []})
        _write_json(tmp / "runtime.json", {**metadata, "elapsed_seconds": time.monotonic() - started,
                                            "bundle_schema_version": BUNDLE_SCHEMA_VERSION})
        _write_json(tmp / "contracts.json", {"schema_version": BUNDLE_SCHEMA_VERSION,
                                               "csv_columns": {k: list(v) for k, v in ARTIFACT_COLUMNS.items()}})
        _write_json(tmp / "daily_audits.json", [{"date": str(a.trade_date), "cash": a.cash,
                    "equity": a.equity, "reason_codes": [x.value for x in a.constraint_reason_codes],
                    "strategy_diagnostics": a.strategy_diagnostics}
                    for a in result.daily_audits])
        _write_csv(tmp / "equity_curve.csv", ({"date": d, "equity": v} for d, v in result.equity_curve.items()))
        _write_csv(tmp / "cash.csv", ({"date": d, "cash": v} for d, v in result.cash_curve.items()))
        _write_csv(tmp / "positions.csv", ({"date": d, "symbol": s, "quantity": q} for d, p in result.positions_by_date.items() for s, q in p.items()))
        marks, last = [], {}
        by_date = {s: {b.trade_date: b for b in bars} for s, bars in data.items()}
        for day in result.equity_curve:
            for symbol in config.universe:
                if day in by_date[symbol]: last[symbol] = by_date[symbol][day].close
                available = symbol in last
                marks.append({"date": day, "symbol": symbol,
                              "close": last[symbol] if available else "", "available": available})
                if result.positions_by_date[day][symbol] and not available:
                    raise ValueError(f"nonzero position has no point-in-time mark: {symbol} on {day}")
            reconstructed = result.cash_curve[day] + sum(
                result.positions_by_date[day][s] * last[s]
                for s in config.universe if s in last)
            if abs(reconstructed - result.equity_curve[day]) > 1e-8:
                raise ValueError(f"daily accounting identity failed on {day}")
        _write_csv(tmp / "marks.csv", marks)
        _write_csv(tmp / "weights.csv", ({"date": a.trade_date, "symbol": s,
                   "target_weight": a.strategy_target.get(s, 0.0)} for a in result.daily_audits for s in config.universe))
        _write_csv(tmp / "orders.csv", ({"date": o.trade_date, "signal_date": o.signal_date, "symbol": o.symbol,
                   "side": o.side, "requested_quantity": o.requested_quantity, "filled_quantity": o.filled_quantity,
                   "status": o.status, "reason": o.reason or ""} for o in result.orders))
        _write_csv(tmp / "generated_orders.csv", ({"date": a.trade_date, "order_id": o.order_id,
                   "symbol": o.intent.symbol, "side": o.intent.side.value, "quantity": o.intent.quantity,
                   "limit_price": o.intent.limit_price} for a in result.daily_audits for o in a.generated_orders))
        _write_csv(tmp / "validated_orders.csv", ({"date": a.trade_date, "order_id": o.order_id,
                   "symbol": o.symbol, "side": o.side.value, "quantity": o.quantity, "limit_price": o.limit_price,
                   "estimated_cost": o.estimated_cost} for a in result.daily_audits for o in a.validated_orders))
        _write_csv(tmp / "fills.csv", ({"date": a.trade_date, "order_id": f.order_id, "symbol": f.symbol,
                   "side": f.side.value, "quantity": f.quantity, "price": f.price, "commission": f.commission,
                   "stamp_duty": f.stamp_duty} for a in result.daily_audits for f in a.fills))
        _write_csv(tmp / "rejections.csv", ({"date": r.trade_date, "order_id": "",
                   "symbol": r.symbol, "reason": r.reason,
                   "reason_code": r.reason_code.value if r.reason_code else "UNCLASSIFIED"}
                   for r in result.rejections))
        _write_csv(tmp / "trades.csv", ({"date": t.trade_date, "symbol": t.symbol, "side": t.side,
                   "quantity": t.quantity, "price": t.price, "commission": t.commission} for t in result.trades))
        _write_csv(tmp / "exposure.csv", ({"date": d, "gross_exposure": sum(abs(x) for x in w.values()),
                   "net_exposure": sum(w.values())} for d, w in result.weights_by_date.items()))
        _write_csv(tmp / "turnover.csv", [{"turnover": result.turnover}])
        peak, drawdowns = 0.0, []
        for day, equity in result.equity_curve.items():
            peak = max(peak, equity)
            drawdowns.append({"date": day, "drawdown": equity / peak - 1})
        _write_csv(tmp / "drawdown.csv", drawdowns)
        _write_csv(tmp / "benchmark_curve.csv", ({"date": d, "equity": e} for d, e in (benchmark.equity_curve.items() if benchmark else ())))
        _write_csv(tmp / "benchmark_cash.csv", ({"date": d, "cash": c} for d, c in (benchmark.cash_curve.items() if benchmark else ())))
        _write_csv(tmp / "benchmark_positions.csv", ({"date": d, "symbol": s, "quantity": q}
                   for d, positions in (benchmark.positions_by_date.items() if benchmark else ())
                   for s, q in positions.items()))
        benchmark_marks, benchmark_last = [], {}
        for day in (benchmark.equity_curve if benchmark else ()):
            for symbol in benchmark_symbols:
                if day in by_date[symbol]:
                    benchmark_last[symbol] = by_date[symbol][day].close
                benchmark_marks.append({"date": day, "symbol": symbol,
                    "close": benchmark_last.get(symbol, ""), "available": symbol in benchmark_last})
        _write_csv(tmp / "benchmark_marks.csv", benchmark_marks)
        _write_csv(tmp / "benchmark_weights.csv", ({"date": audit.trade_date, "symbol": symbol,
                   "target_weight": audit.strategy_target.get(symbol, 0.0)}
                   for audit in (benchmark.daily_audits if benchmark else ()) for symbol in benchmark_symbols))
        _write_csv(tmp / "benchmark_orders.csv", ({"date": order.trade_date, "signal_date": order.signal_date,
                   "symbol": order.symbol, "side": order.side, "requested_quantity": order.requested_quantity,
                   "filled_quantity": order.filled_quantity, "status": order.status, "reason": order.reason or ""}
                   for order in (benchmark.orders if benchmark else ())))
        _write_csv(tmp / "benchmark_fills.csv", ({"date": audit.trade_date, "order_id": fill.order_id,
                   "symbol": fill.symbol, "side": fill.side.value, "quantity": fill.quantity,
                   "price": fill.price, "commission": fill.commission, "stamp_duty": fill.stamp_duty}
                   for audit in (benchmark.daily_audits if benchmark else ()) for fill in audit.fills))
        _write_csv(tmp / "parameter_results.csv", [{"applicable": False, "parameters": "{}",
                   "validation_metrics": "{}", "error": "no parameter selection configured"}])
        artifacts = {name: sha256_file(tmp / name) for name in sorted(set(ARTIFACT_COLUMNS) | set(JSON_ARTIFACTS))}
        _write_json(tmp / "artifact_manifest.json", {"schema_version": BUNDLE_SCHEMA_VERSION, "artifacts": artifacts})
        root.parent.mkdir(parents=True, exist_ok=True)
        os.rename(tmp, root)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return root
