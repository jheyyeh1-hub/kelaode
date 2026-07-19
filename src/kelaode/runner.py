"""Configuration-only shared experiment runner and artifact writer."""
from __future__ import annotations
import csv, json, time
from datetime import date
from dataclasses import asdict
from pathlib import Path

from .experiment import ExperimentConfig, experiment_identity, experiment_metadata, initialize_output
from .experiment_metrics import performance_metrics
from .market_data import read_daily_bars
from .portfolio import PortfolioBacktestConfig, PortfolioBacktester, EqualWeightBuyAndHold, CrossSectionalMomentumStrategy
from .snapshot import SnapshotManifest

STRATEGIES = {"EqualWeightBuyAndHold": EqualWeightBuyAndHold,
              "CrossSectionalMomentumStrategy": CrossSectionalMomentumStrategy}

def _csv(path, columns, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns); writer.writeheader(); writer.writerows(rows)

def run_experiment(config: ExperimentConfig) -> Path:
    started = time.time()
    manifest_path = Path(config.data_manifest)
    manifest = SnapshotManifest.load(manifest_path)
    manifest.validate(config.data_root, expected_symbols=config.universe,
                      allow_mixed_adjustments=config.allow_mixed_adjustments)
    metadata = experiment_metadata(config, manifest_path)
    identity = experiment_identity(config, manifest, metadata)
    existing = Path(config.output_directory) / identity["experiment_id"]
    if existing.exists():
        return initialize_output(config, metadata, identity=identity)
    start, end = date.fromisoformat(config.start_date), date.fromisoformat(config.end_date)
    data = {entry.symbol: [bar for bar in read_daily_bars(Path(config.data_root) / entry.relative_path)
                           if start <= bar.trade_date <= end]
            for entry in manifest.entries}
    try:
        strategy_cls = STRATEGIES[config.strategy_class]
    except KeyError as exc:
        raise ValueError(f"unregistered strategy_class: {config.strategy_class}") from exc
    parameters = dict(config.strategy_parameters)
    if strategy_cls in (EqualWeightBuyAndHold, CrossSectionalMomentumStrategy):
        parameters["symbols"] = config.universe
    strategy = strategy_cls(**parameters)
    merged = {**config.constraint_parameters, **config.execution_parameters,
              **config.fee_parameters, **config.slippage_parameters, "initial_cash": config.initial_cash}
    allowed = set(PortfolioBacktestConfig.__dataclass_fields__)
    unknown = set(merged) - allowed
    if unknown:
        raise ValueError(f"unsupported backtest configuration fields: {sorted(unknown)}")
    result = PortfolioBacktester(PortfolioBacktestConfig(**merged)).run(data, strategy)
    root = initialize_output(config, metadata, identity=identity)
    (root / "data_manifest.json").write_text(json.dumps(manifest.as_dict(), indent=2) + "\n")
    metrics = performance_metrics(list(result.equity_curve.values()))
    (root / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    (root / "runtime.json").write_text(json.dumps({**metadata, "elapsed_seconds": time.time()-started}, indent=2)+"\n")
    _csv(root/"equity_curve.csv", ["date","equity"], ({"date":d,"equity":v} for d,v in result.equity_curve.items()))
    _csv(root/"cash.csv", ["date","cash"], ({"date":d,"cash":v} for d,v in result.cash_curve.items()))
    _csv(root/"positions.csv", ["date","symbol","quantity"], ({"date":d,"symbol":s,"quantity":q} for d,p in result.positions_by_date.items() for s,q in p.items()))
    _csv(root/"weights.csv", ["date","symbol","target_weight"], ({"date":a.trade_date,"symbol":s,"target_weight":w} for a in result.daily_audits for s,w in a.strategy_target.items()))
    _csv(root/"trades.csv", ["date","symbol","side","quantity","price","commission"], ({"date":t.trade_date,"symbol":t.symbol,"side":t.side,"quantity":t.quantity,"price":t.price,"commission":t.commission} for t in result.trades))
    _csv(root/"orders.csv", ["date","signal_date","symbol","side","requested_quantity","filled_quantity","status","reason"], ({"date":o.trade_date,"signal_date":o.signal_date,"symbol":o.symbol,"side":o.side,"requested_quantity":o.requested_quantity,"filled_quantity":o.filled_quantity,"status":o.status,"reason":o.reason} for o in result.orders))
    _csv(root/"fills.csv", ["order_id","symbol","side","quantity","price","commission","stamp_duty"], ({"order_id":f.order_id,"symbol":f.symbol,"side":f.side.value,"quantity":f.quantity,"price":f.price,"commission":f.commission,"stamp_duty":f.stamp_duty} for f in result.fills))
    _csv(root/"rejections.csv", ["date","symbol","reason","reason_code"], ({"date":r.trade_date,"symbol":r.symbol,"reason":r.reason,"reason_code":r.reason_code.value if r.reason_code else ""} for r in result.rejections))
    _csv(root/"validated_orders.csv", ["order_id","symbol","side","quantity"], ({"order_id":o.order_id,"symbol":o.symbol,"side":o.side.value,"quantity":o.quantity} for o in result.validated_orders))
    peak=0.0; draw=[]
    for d,e in result.equity_curve.items(): peak=max(peak,e); draw.append({"date":d,"drawdown":e/peak-1})
    _csv(root/"drawdown.csv", ["date","drawdown"], draw)
    _csv(root/"exposure.csv", ["date","gross_exposure","net_exposure"], ({"date":d,"gross_exposure":sum(abs(x) for x in w.values()),"net_exposure":sum(w.values())} for d,w in result.weights_by_date.items()))
    _csv(root/"turnover.csv", ["turnover"], [{"turnover":result.turnover}])
    (root/"daily_audits.json").write_text(json.dumps([{"date":str(a.trade_date),"cash":a.cash,"equity":a.equity} for a in result.daily_audits], indent=2)+"\n")
    return root
