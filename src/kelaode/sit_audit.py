"""Read-only audit helpers for the SIT real-data validation.

The audit deliberately consumes the frozen PR #9 configuration and cached bars.  It
does not choose parameters or alter the strategy rule.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import platform
import subprocess
import sys
import time
from collections import defaultdict, deque
from datetime import date
from importlib import metadata
from pathlib import Path
from statistics import mean, stdev
from typing import Any, Iterable, Mapping

from .experiment_metrics import performance_metrics
from .sit_validation import UNIVERSE, load_data, run_sit, write_csv


ASSET_CLASS = {
    "510300": "broad_equity",
    "510500": "broad_equity",
    "159915": "broad_equity",
    "512100": "sector_theme",
    "512880": "sector_theme",
    "512480": "sector_theme",
    "518880": "gold",
    "513100": "overseas",
    "511010": "bond",
}


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def reference_metrics(
    equity: Iterable[float], trades: Iterable[Mapping[str, Any]]
) -> dict[str, float]:
    """Independent, intentionally small metric calculator."""
    values = [float(x) for x in equity]
    rows = list(trades)
    returns = [b / a - 1 for a, b in zip(values, values[1:])]
    peak, max_dd = values[0], 0.0
    for value in values:
        peak = max(peak, value)
        max_dd = min(max_dd, value / peak - 1)
    vol = stdev(returns) * math.sqrt(252) if len(returns) > 1 else 0.0
    fees = sum(float(t.get("commission", 0)) for t in rows)
    notional = sum(abs(float(t["quantity"]) * float(t["price"])) for t in rows)
    return {
        "total_return": values[-1] / values[0] - 1,
        "cagr": (values[-1] / values[0]) ** (252 / max(1, len(values) - 1)) - 1,
        "annualized_volatility": vol,
        "sharpe": (mean(returns) * 252 / vol) if vol else 0.0,
        "max_drawdown": max_dd,
        "turnover": notional / mean(values),
        "total_commission": fees,
    }


def audit_data(
    cache: Path, out: Path, data
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    all_dates = sorted({b.trade_date for bars in data.values() for b in bars})
    rows = []
    adjustments = set()
    for symbol, bars in data.items():
        source = (cache / f"{symbol}.source").read_text().strip()
        adjustment = "qfq" if "qfq" in source else "unadjusted"
        adjustments.add(adjustment)
        dates = [b.trade_date for b in bars]
        jumps = [
            (str(b.trade_date), b.close / a.close - 1)
            for a, b in zip(bars, bars[1:])
            if a.close and abs(b.close / a.close - 1) > 0.15
        ]
        expected = [d for d in all_dates if dates[0] <= d <= dates[-1]]
        bad_ohlc = sum(
            not (b.low <= min(b.open, b.close) <= max(b.open, b.close) <= b.high)
            for b in bars
        )
        rows.append(
            {
                "symbol": symbol,
                "data_source": source.split(";")[0],
                "adjustment": adjustment,
                "first_date": dates[0],
                "last_date": dates[-1],
                "missing_trading_days": len(set(expected) - set(dates)),
                "duplicate_dates": len(dates) - len(set(dates)),
                "non_positive_prices": sum(min(b.open, b.close) <= 0 for b in bars),
                "abnormal_jumps_over_15pct": len(jumps),
                "abnormal_jump_examples": json.dumps(jumps[:5], ensure_ascii=False),
                "fallback_used": "Sina" in source,
                "mixed_adjustment_universe": len(adjustments) > 1,
                "invalid_ohlc_rows": bad_ohlc,
                "invalid_volume_rows": sum(
                    not math.isfinite(b.volume) or b.volume < 0 for b in bars
                ),
                "corporate_action_risk": "requires provider corporate-action ledger; unavailable",
                "file_sha256": _sha(cache / f"{symbol}.csv"),
            }
        )
    mixed = len({r["adjustment"] for r in rows}) > 1
    for row in rows:
        row["mixed_adjustment_universe"] = mixed
    consistency = {
        "mixed_adjustment": mixed,
        "eastmoney_qfq_symbols": [
            r["symbol"] for r in rows if r["adjustment"] == "qfq"
        ],
        "sina_unadjusted_symbols": [
            r["symbol"] for r in rows if r["adjustment"] == "unadjusted"
        ],
        "uniform_source_runs": {
            "qfq_only": "not comparable to nine-ETF run: universe changes",
            "unadjusted_all": "not run: uniform reliable unadjusted cache unavailable",
            "qfq_all": "not run: Eastmoney qfq unavailable for fallback symbols",
        },
        "investment_conclusion_valid": False,
        "reason": "The frozen result mixes qfq Eastmoney and unadjusted Sina series.",
    }
    write_csv(out / "data_audit.csv", rows)
    (out / "data_source_consistency.json").write_text(
        json.dumps(consistency, indent=2) + "\n"
    )
    return rows, consistency


def reconcile(out: Path) -> list[dict[str, Any]]:
    eq_rows = _read_csv(out.parent / "sit_validation" / "test_equity.csv")
    trades = _read_csv(out.parent / "sit_validation" / "test_trades.csv")
    main = json.loads((out.parent / "sit_validation" / "test_metrics.json").read_text())
    ref = reference_metrics((r["equity"] for r in eq_rows), trades)
    rows = []
    for metric, value in ref.items():
        primary = float(main[metric] if metric in main else 0)
        rows.append(
            {
                "metric": metric,
                "primary": primary,
                "reference": value,
                "absolute_difference": abs(primary - value),
                "within_1e-10": abs(primary - value) <= 1e-10,
            }
        )
    write_csv(out / "metric_reconciliation.csv", rows)
    return rows


def trade_attribution(
    trades: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    lots: dict[str, deque[list[float]]] = defaultdict(deque)
    stats = defaultdict(
        lambda: {"holding_days": 0, "trade_count": 0, "realized": 0.0, "profits": []}
    )
    closed = []
    for t in trades:
        symbol, side = t["symbol"], t["side"]
        qty, price, fee = int(t["quantity"]), float(t["price"]), float(t["commission"])
        day = date.fromisoformat(t["trade_date"])
        stats[symbol]["trade_count"] += 1
        if side == "buy":
            lots[symbol].append([qty, price + fee / qty, day.toordinal()])
        else:
            remaining = qty
            while remaining and lots[symbol]:
                lot = lots[symbol][0]
                used = min(remaining, int(lot[0]))
                pnl = used * (price - fee / qty - lot[1])
                holding = day.toordinal() - int(lot[2])
                closed.append(
                    {
                        "symbol": symbol,
                        "buy_date_ordinal": int(lot[2]),
                        "sell_date": day,
                        "quantity": used,
                        "pnl": pnl,
                        "holding_days": holding,
                    }
                )
                stats[symbol]["realized"] += pnl
                stats[symbol]["profits"].append(pnl)
                stats[symbol]["holding_days"] += holding * used
                lot[0] -= used
                remaining -= used
                if not lot[0]:
                    lots[symbol].popleft()
    rows = []
    for symbol in UNIVERSE:
        s = stats[symbol]
        ps = s["profits"]
        rows.append(
            {
                "symbol": symbol,
                "asset_class": ASSET_CLASS[symbol],
                "holding_days_quantity_weighted": s["holding_days"],
                "trade_count": s["trade_count"],
                "realized_pnl": s["realized"],
                "unrealized_pnl": "not independently available at final open lots",
                "max_trade_contribution": max(ps, default=0),
                "max_trade_loss": min(ps, default=0),
            }
        )
    return rows, closed


def generate_audit(base: Path = Path(".")) -> Path:
    started = time.perf_counter()
    out = base / "results/sit_audit"
    out.mkdir(parents=True, exist_ok=True)
    validation = base / "results/sit_validation"
    data = load_data(base / "data/market/etf_daily", validation)
    data_rows, consistency = audit_data(base / "data/market/etf_daily", out, data)
    selected = json.loads((validation / "selected_parameters.json").read_text())
    metadata_rows = _read_csv(validation / "test_equity.csv")
    start, end = (
        date.fromisoformat(metadata_rows[0]["date"]),
        date.fromisoformat(metadata_rows[-1]["date"]),
    )
    rerun, rerun_eq, result = run_sit(data, selected, start, end, artifacts=True)
    frozen = json.loads((validation / "test_metrics.json").read_text())
    keys = ("total_return", "cagr", "sharpe", "max_drawdown")
    diffs = {k: abs(float(frozen[k]) - float(rerun[k])) for k in keys}
    # PR #9 deliberately ignores its result directory.  Its only durable frozen
    # test claim is the value in the committed validation report.
    committed_claims = {
        "total_return": 0.315557,
        "cagr": 0.149517,
        "sharpe": 0.571,
        "max_drawdown": -0.426946,
    }
    committed_diffs = {k: abs(float(rerun[k]) - v) for k, v in committed_claims.items()}
    reproducible = all(v <= 1e-6 for v in committed_diffs.values())
    packages = {p: metadata.version(p) for p in ("akshare", "pandas", "matplotlib")}
    repro = {
        "reproducible": reproducible,
        "verdict": "FAIL" if not reproducible else "PASS",
        "reason": "PR #9 raw cache and results are ignored; rerun does not match its committed report.",
        "rerun_is_internally_repeatable": all(v <= 1e-10 for v in diffs.values()),
        "internal_tolerance": 1e-10,
        "internal_metric_differences": diffs,
        "committed_claims": committed_claims,
        "rerun_metrics": {k: rerun[k] for k in keys},
        "claim_absolute_differences": committed_diffs,
        "runtime_seconds": time.perf_counter() - started,
        "peak_memory_kb": __import__("resource")
        .getrusage(__import__("resource").RUSAGE_SELF)
        .ru_maxrss,
        "git_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "python": sys.version,
        "platform": platform.platform(),
        "dependencies": packages,
    }
    (out / "reproducibility.json").write_text(json.dumps(repro, indent=2) + "\n")
    reconcile(out)

    trades = _read_csv(validation / "test_trades.csv")
    attr, closed = trade_attribution(trades)
    write_csv(out / "return_attribution_by_symbol.csv", attr)
    equity = [
        (date.fromisoformat(r["date"]), float(r["equity"])) for r in metadata_rows
    ]
    period_rows = []
    for label, key in (
        ("month", lambda d: d.strftime("%Y-%m")),
        ("year", lambda d: d.strftime("%Y")),
    ):
        groups = defaultdict(list)
        for d, v in equity:
            groups[key(d)].append(v)
        for period, values in groups.items():
            m = performance_metrics(values)
            period_rows.append(
                {
                    "frequency": label,
                    "period": period,
                    "return": m["total_return"],
                    "max_drawdown": m["max_drawdown"],
                }
            )
    write_csv(out / "return_attribution_by_period.csv", period_rows)
    ranked = sorted(closed, key=lambda x: x["pnl"], reverse=True)
    top_rows = [{**x, "rank_group": "top_profit"} for x in ranked[:5]] + [
        {**x, "rank_group": "top_loss"} for x in ranked[-5:]
    ]
    total_profit = sum(x["pnl"] for x in closed)
    for n in (1, 3):
        top_rows.append(
            {
                "symbol": "SUMMARY",
                "pnl": total_profit - sum(x["pnl"] for x in ranked[:n]),
                "rank_group": f"realized_pnl_without_top_{n}",
            }
        )
    write_csv(out / "top_trades.csv", top_rows)

    folds = _read_csv(validation / "walk_forward_folds.csv")
    wf_rows = []
    for f in folds:
        tr = float(f["test_total_return"])
        wf_rows.append(
            {
                **f,
                "benchmark_test_return": "not saved per fold in PR #9",
                "beat_510300": "not auditable from saved artifacts",
                "beat_equal_weight": "not auditable from saved artifacts",
                "profitable": tr > 0,
                "data_source": "mixed Eastmoney qfq/Sina unadjusted",
                "holding_count": "not saved",
                "trade_count": f.get("test_trade_count", ""),
            }
        )
    write_csv(out / "walk_forward_diagnostics.csv", wf_rows)

    # Diagnostic one-factor perturbations only; no selection is performed.
    sensitivities = []
    for name, values in {
        "momentum_lookback": [101, 151],
        "trend_window": [160, 240],
        "volatility_lookback": [48, 72],
        "max_weight": [0.8],
    }.items():
        for value in values:
            p = dict(selected)
            p[name] = value
            m, _, _ = run_sit(data, p, start, end)
            sensitivities.append(
                {
                    "parameter": name,
                    "frozen_value": selected[name],
                    "diagnostic_value": value,
                    "total_return": m["total_return"],
                    "sharpe": m["sharpe"],
                    "max_drawdown": m["max_drawdown"],
                    "used_for_selection": False,
                }
            )
    write_csv(out / "local_parameter_sensitivity.csv", sensitivities)

    peak = (-math.inf, start)
    trough = (0.0, start)
    for d, v in equity:
        if v > peak[0]:
            peak = (v, d)
        dd = v / peak[0] - 1
        if dd < trough[0]:
            trough = (dd, d, peak[1])
    (out / "drawdown_episode.md").write_text(
        f"# Maximum drawdown episode\n\nPeak date: {trough[2]}; trough date: {trough[1]}; drawdown: {trough[0]:.4%}. The frozen artifacts do not save daily positions, so exact trough holdings and trigger cannot be reconstructed without rerunning artifacts; this is an auditability failure.\n"
    )
    return out
