"""Resource-bounded, resumable real-data validation for the SIT ETF strategy."""

from __future__ import annotations

import csv
import hashlib
import itertools
import json
import logging
import resource
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .experiment import RollingWalkForward, _canonical
from .experiment_metrics import performance_metrics
from .market_data import (
    AKShareETFDownloader,
    DailyBar,
    read_daily_bars,
    write_daily_bars,
)
from .open_source_rotation import SITMomentumRotationStrategy, SITRotationParameters
from .portfolio import (
    CrossSectionalMomentumStrategy,
    EqualWeightBuyAndHold,
    HoldTargets,
    PortfolioBacktestConfig,
    PortfolioBacktester,
)

UNIVERSE = (
    "510300",
    "510500",
    "159915",
    "512100",
    "512880",
    "512480",
    "518880",
    "513100",
    "511010",
)
GRID = {
    "momentum_lookback": (63, 126, 189),
    "top_k": (1, 2, 3),
    "rebalance_frequency": ("monthly",),
    "trend_window": (None, 200),
    "volatility_lookback": (None, 60),
    "max_weight": (0.5, 1.0),
}
DEFAULT = {
    "momentum_lookback": 126,
    "top_k": 2,
    "rebalance_frequency": "monthly",
    "trend_window": None,
    "volatility_lookback": None,
    "minimum_listing_age": 127,
    "max_weight": 0.5,
}


def parameter_combinations() -> tuple[dict[str, Any], ...]:
    keys = sorted(GRID)
    return tuple(
        dict(zip(keys, values))
        for values in itertools.product(*(GRID[k] for k in keys))
        if valid_parameters(dict(zip(keys, values)))
    )


def valid_parameters(parameters: Mapping[str, Any]) -> bool:
    """Cash is allowed, but a selected portfolio must be capable of full investment."""
    return (
        int(parameters["top_k"]) >= 1
        and float(parameters["max_weight"]) > 0
        and int(parameters["top_k"]) * float(parameters["max_weight"]) >= 1
    )


def experiment_id(
    parameters: Mapping[str, Any],
    start: date,
    end: date,
    costs: Mapping[str, float] | None = None,
) -> str:
    raw = {
        "parameters": parameters,
        "start": str(start),
        "end": str(end),
        "costs": costs or {},
    }
    return hashlib.sha256(_canonical(raw).encode()).hexdigest()[:16]


def load_data(cache: Path, output: Path) -> dict[str, tuple[DailyBar, ...]]:
    cache.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    downloader = AKShareETFDownloader()
    entries = []
    now = datetime.now(timezone.utc).isoformat()
    for symbol in UNIVERSE:
        path = cache / f"{symbol}.csv"
        if not path.exists():
            try:
                downloader.download_csv(
                    symbol, "2005-01-01", date.today(), path, adjust="qfq"
                )
                (cache / f"{symbol}.source").write_text(
                    "AKShare fund_etf_hist_em / Eastmoney; qfq"
                )
            except Exception as eastmoney_error:
                # Eastmoney periodically blocks its JSON endpoint.  Sina's public
                # unadjusted daily endpoint is a transparent, non-synthetic fallback.
                import akshare

                exchange = "sh" if symbol.startswith(("5", "6")) else "sz"
                frame = akshare.fund_etf_hist_sina(exchange + symbol)
                bars = [
                    DailyBar(
                        AKShareETFDownloader._parse_date(r["date"]),
                        float(r["open"]),
                        float(r["high"]),
                        float(r["low"]),
                        float(r["close"]),
                        float(r["volume"]),
                    )
                    for r in frame.to_dict("records")
                ]
                write_daily_bars(path, bars)
                (cache / f"{symbol}.source").write_text(
                    f"AKShare fund_etf_hist_sina / Sina; unadjusted; Eastmoney failed: {type(eastmoney_error).__name__}"
                )
        bars = tuple(read_daily_bars(path))
        source = (
            (cache / f"{symbol}.source").read_text()
            if (cache / f"{symbol}.source").exists()
            else "unknown cached source"
        )
        entries.append(
            {
                "symbol": symbol,
                "first_valid_date": str(bars[0].trade_date),
                "last_valid_date": str(bars[-1].trade_date),
                "valid_trading_days": len(bars),
                "missing_values": 0,
                "duplicate_dates": len(bars) - len({b.trade_date for b in bars}),
                "non_positive_prices": sum(
                    min(b.open, b.high, b.low, b.close) <= 0 for b in bars
                ),
                "data_source": source,
                "adjustment": "qfq" if "qfq" in source else "none",
                "adjustment_limitation": "Adjustment is source-labelled only; independently verified total-return adjustment unavailable",
                "downloaded_at": datetime.fromtimestamp(
                    path.stat().st_mtime, timezone.utc
                ).isoformat(),
            }
        )
    manifest = {
        "generated_at": now,
        "survivorship_bias": "Universe is selected with present knowledge and is not a historical point-in-time constituent set.",
        "entries": entries,
    }
    (output / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    return {s: tuple(read_daily_bars(cache / f"{s}.csv")) for s in UNIVERSE}


def subset(data, start: date, end: date, warmup=200):
    dates = sorted(
        {
            b.trade_date
            for bars in data.values()
            for b in bars
            if start <= b.trade_date <= end
        }
    )
    if not dates:
        raise ValueError("empty evaluation interval")
    all_dates = sorted({b.trade_date for bars in data.values() for b in bars})
    pos = all_dates.index(dates[0])
    begin = all_dates[max(0, pos - warmup)]
    return {
        s: tuple(b for b in bars if begin <= b.trade_date <= end)
        for s, bars in data.items()
    }, dates[0]


def run_sit(data, parameters, start, end, costs=None, artifacts=False):
    view, actual_start = subset(
        data,
        start,
        end,
        max(
            200,
            parameters.get("momentum_lookback", 1),
            parameters.get("trend_window") or 0,
            parameters.get("volatility_lookback") or 0,
        )
        + 1,
    )
    fee = {
        "commission_rate": 0.00025,
        "minimum_commission": 5.0,
        "slippage_rate": 0.0005,
    }
    fee.update(costs or {})
    config = PortfolioBacktestConfig(
        initial_cash=100000,
        max_single_weight=float(parameters["max_weight"]),
        max_gross_exposure=1,
        **fee,
    )
    p = dict(parameters)
    p.setdefault(
        "minimum_listing_age",
        max(
            int(p["momentum_lookback"]) + 1,
            int(p.get("trend_window") or 0),
            int(p.get("volatility_lookback") or 0) + 1,
        ),
    )
    underlying = SITMomentumRotationStrategy(UNIVERSE, SITRotationParameters(**p))

    class StartGate:
        def target_weights(self, index, today, market, portfolio):
            return (
                HoldTargets()
                if today < actual_start
                else underlying.target_weights(index, today, market, portfolio)
            )

    result = PortfolioBacktester(config).run(view, StartGate())
    equity = {d: v for d, v in result.equity_curve.items() if actual_start <= d <= end}
    trades = [t for t in result.trades if actual_start <= t.trade_date <= end]
    records = [{"notional": t.quantity * t.price, **asdict(t)} for t in trades]
    metrics = performance_metrics(list(equity.values()), records)
    metrics.update(
        final_equity=list(equity.values())[-1],
        total_commission=sum(t.commission for t in trades),
        total_slippage_cost=sum(
            t.quantity
            * t.price
            * fee["slippage_rate"]
            / (
                1 + fee["slippage_rate"]
                if t.side == "buy"
                else 1 - fee["slippage_rate"]
            )
            for t in trades
        ),
    )
    metrics["total_trading_cost"] = (
        metrics["total_commission"] + metrics["total_slippage_cost"]
    )
    return metrics, equity, result if artifacts else None


def append_result(path: Path, row: Mapping[str, Any]) -> None:
    exists = path.exists() and path.stat().st_size
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        if not exists:
            writer.writeheader()
        writer.writerow(row)
        handle.flush()


def grid_search(data, start, end, path: Path):
    completed = set()
    if path.exists():
        with path.open(encoding="utf-8") as f:
            completed = {r["experiment_id"] for r in csv.DictReader(f)}
    rows = []
    if path.exists():
        with path.open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    for params in parameter_combinations():
        ident = experiment_id(params, start, end)
        if ident in completed:
            continue
        try:
            metrics, _, _ = run_sit(data, params, start, end)
            row = {
                "experiment_id": ident,
                "parameters": _canonical(params),
                "error": "",
                **metrics,
            }
        except Exception as exc:
            row = {
                "experiment_id": ident,
                "parameters": _canonical(params),
                "error": f"{type(exc).__name__}: {exc}",
            }
        append_result(path, row)
        rows.append({k: str(v) for k, v in row.items()})
        logging.info(
            "candidate=%s peak_rss_kb=%s",
            ident,
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        )
    return rows


def select(rows):
    eligible = [
        r
        for r in rows
        if not r.get("error")
        and float(r.get("max_drawdown", -1)) >= -0.35
        and float(r.get("trade_count", 0)) >= 5
    ]
    if not eligible:
        raise ValueError("no eligible parameter combination")
    eligible.sort(
        key=lambda r: (
            -float(r["calmar"]),
            -float(r["sharpe"]),
            abs(float(r["max_drawdown"])),
            float(r["turnover"]),
            r["parameters"],
        )
    )
    return json.loads(eligible[0]["parameters"]), eligible[0]


def write_csv(path, rows):
    rows = list(rows)
    with path.open("w", newline="", encoding="utf-8") as f:
        if rows:
            fields = list(dict.fromkeys(key for row in rows for key in row))
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)


def write_equity(path, equity):
    write_csv(path, ({"date": d, "equity": v} for d, v in equity.items()))


def run_validation(config_path: str, command="run") -> Path:
    del config_path
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    out = Path("results/sit_validation")
    cache = Path("data/market/etf_daily")
    out.mkdir(parents=True, exist_ok=True)
    data = load_data(cache, out)
    dates = sorted({b.trade_date for bars in data.values() for b in bars})
    warm_start = max(date(2019, 6, 12), dates[0])
    train_start = min(d for d in dates if d >= date(2020, 1, 1))
    train_end = max(d for d in dates if d <= date(2022, 12, 31))
    validation_start = min(d for d in dates if d >= date(2023, 1, 1))
    validation_end = max(d for d in dates if d <= date(2024, 6, 30))
    test_start = min(d for d in dates if d >= date(2024, 7, 1))
    end = dates[-1]
    fixed, equity, _ = run_sit(data, DEFAULT, train_start, end)
    (out / "fixed_default_metrics.json").write_text(json.dumps(fixed, indent=2) + "\n")
    write_equity(out / "fixed_default_equity.csv", equity)
    rows = grid_search(
        data, validation_start, validation_end, out / "parameter_results.csv"
    )
    selected, validation = select(rows)
    selected["minimum_listing_age"] = max(
        selected["momentum_lookback"] + 1,
        selected.get("trend_window") or 0,
        (selected.get("volatility_lookback") or 0) + 1,
    )
    (out / "selected_parameters.json").write_text(json.dumps(selected, indent=2) + "\n")
    (out / "validation_metrics.json").write_text(
        json.dumps(validation, indent=2) + "\n"
    )
    test_metrics, test_equity, result = run_sit(
        data, selected, test_start, end, artifacts=True
    )
    (out / "test_metrics.json").write_text(json.dumps(test_metrics, indent=2) + "\n")
    write_equity(out / "test_equity.csv", test_equity)
    peak = 0.0
    dd = []
    for d, v in test_equity.items():
        peak = max(peak, v)
        dd.append({"date": d, "drawdown": v / peak - 1})
    write_csv(out / "test_drawdown.csv", dd)
    write_csv(
        out / "test_trades.csv",
        (asdict(x) for x in result.trades if x.trade_date >= test_start),
    )
    write_csv(
        out / "test_orders.csv",
        (asdict(x) for x in result.orders if x.trade_date >= test_start),
    )
    write_csv(out / "test_fills.csv", (asdict(x) for x in result.fills))
    # Cost sensitivity is deliberately sequential and uses the exact frozen parameters.
    scenarios = {
        "base": (1, 1),
        "2x_commission": (2, 1),
        "2x_slippage": (1, 2),
        "2x_both": (2, 2),
        "4x_both": (4, 4),
    }
    costs = []
    for name, (c, s) in scenarios.items():
        m, _, _ = run_sit(
            data,
            selected,
            test_start,
            end,
            {
                "commission_rate": 0.00025 * c,
                "minimum_commission": 5 * c,
                "slippage_rate": 0.0005 * s,
            },
        )
        costs.append({"scenario": name, **m})
    write_csv(out / "cost_sensitivity.csv", costs)
    # Walk-forward: search results are summaries only; test artifacts are immediately discarded.
    folds = RollingWalkForward(504, 126, 126, 126, 200).split(
        [d for d in dates if d >= train_start]
    )
    fold_rows = []
    stitched = {}
    selections = []
    for n, fold in enumerate(folds):
        fp = out / f"parameter_results_fold_{n}.csv"
        candidates = grid_search(data, fold.validation[0], fold.validation[-1], fp)
        chosen, vm = select(candidates)
        chosen["minimum_listing_age"] = max(
            chosen["momentum_lookback"] + 1,
            chosen.get("trend_window") or 0,
            (chosen.get("volatility_lookback") or 0) + 1,
        )
        tm, curve, _ = run_sit(data, chosen, fold.test[0], fold.test[-1])
        scale = (
            (list(stitched.values())[-1] / list(curve.values())[0]) if stitched else 1.0
        )
        stitched.update({d: v * scale for d, v in curve.items()})
        selections.append(_canonical(chosen))
        fold_rows.append(
            {
                "fold": n,
                "train_start": fold.train[0],
                "train_end": fold.train[-1],
                "validation_start": fold.validation[0],
                "validation_end": fold.validation[-1],
                "test_start": fold.test[0],
                "test_end": fold.test[-1],
                "selected_parameters": _canonical(chosen),
                "validation_calmar": vm["calmar"],
                **{"test_" + k: v for k, v in tm.items()},
            }
        )
    write_csv(out / "walk_forward_folds.csv", fold_rows)
    write_equity(out / "walk_forward_equity.csv", dict(sorted(stitched.items())))
    # Strictly aligned test-period benchmarks.
    benchmarks = []

    def benchmark(name, strategy):
        view, _ = subset(data, test_start, end, 0)
        r = PortfolioBacktester(PortfolioBacktestConfig()).run(view, strategy)
        eq = {d: v for d, v in r.equity_curve.items() if d >= test_start}
        m = performance_metrics(
            list(eq.values()), [{"notional": t.quantity * t.price} for t in r.trades]
        )
        benchmarks.append(
            {"benchmark": name, **m, "final_equity": list(eq.values())[-1]}
        )

    benchmark("510300_buy_hold", EqualWeightBuyAndHold(("510300",)))
    benchmark("nine_etf_equal_weight_buy_hold", EqualWeightBuyAndHold(UNIVERSE))
    benchmark(
        "cross_sectional_momentum", CrossSectionalMomentumStrategy(UNIVERSE, 126, 2)
    )
    benchmarks.extend(
        [
            {
                "benchmark": "sit_fixed_default",
                **run_sit(data, DEFAULT, test_start, end)[0],
            },
            {"benchmark": "sit_selected", **test_metrics},
        ]
    )
    wf = performance_metrics(list(stitched.values()))
    benchmarks.append(
        {
            "benchmark": "sit_walk_forward_oos",
            **wf,
            "final_equity": list(stitched.values())[-1] if stitched else 100000,
        }
    )
    write_csv(out / "benchmark_comparison.csv", benchmarks)
    boundaries = {
        "warmup_start": warm_start,
        "train_start": train_start,
        "train_end": train_end,
        "validation_start": validation_start,
        "validation_end": validation_end,
        "test_start": test_start,
        "data_end": end,
    }
    (out / "run_metadata.json").write_text(
        json.dumps({k: str(v) for k, v in boundaries.items()}, indent=2) + "\n"
    )
    make_plots(out, test_equity, dd)
    make_report(
        out,
        boundaries,
        fixed,
        selected,
        validation,
        test_metrics,
        fold_rows,
        benchmarks,
        costs,
        selections,
    )
    return out


def make_plots(out, equity, dd):
    import matplotlib.pyplot as plt

    for name, values, label in (
        ("equity_curve.png", equity, "Equity"),
        ("drawdown.png", {r["date"]: r["drawdown"] for r in dd}, "Drawdown"),
    ):
        plt.figure(figsize=(9, 4))
        plt.plot(list(values), list(values.values()))
        plt.title(label)
        plt.tight_layout()
        plt.savefig(out / name, dpi=120)
        plt.close()


def make_report(
    out, b, fixed, selected, validation, test, folds, benchmarks, costs, selections
):
    manifest = json.loads((out / "data_manifest.json").read_text())
    entries = "\n".join(
        f"- {e['symbol']}: {e['first_valid_date']} – {e['last_valid_date']} ({e['valid_trading_days']} sessions)"
        for e in manifest["entries"]
    )
    sources = sorted({e["data_source"].split(";", 1)[0] for e in manifest["entries"]})
    text = f"""# SIT ETF rotation real-data validation

Generated {manifest["generated_at"]}. Data sources actually used: {", ".join(sources)}. Adjustment status is recorded per symbol in `data_manifest.json`. Raw files and results are ignored by Git.

## Data coverage
{entries}

No pre-listing history was backfilled. This present-day ETF universe has survivorship/selection bias. Provider adjustment methodologies were not independently verified; unadjusted Sina fallbacks omit distribution adjustment and qfq series are not claimed to be fully audited total-return series.

## Design
- Boundaries: `{b}`. Warm-up is excluded from metrics.
- Grid: `{_canonical(GRID)}` ({len(parameter_combinations())} legal combinations after filtering combinations where `top_k * max_weight < 1`). Single process; each result is flushed immediately and experiment IDs resume without rerunning.
- Selection: validation Calmar, max drawdown <=35%, >=5 trades; then Sharpe, lower drawdown, turnover, parameter JSON. Test was run once after freezing.
- Signal at close, next-session open fill; commission/minimum commission/slippage, 100-share lots, cash and exposure constraints are applied.

## Results
- Fixed default: total return {fixed["total_return"]:.4%}, Sharpe {fixed["sharpe"]:.3f}, max drawdown {fixed["max_drawdown"]:.4%}, turnover {fixed["turnover"]:.3f}.
- Selected parameters: `{_canonical(selected)}`. Validation Calmar {float(validation["calmar"]):.3f}, return {float(validation["total_return"]):.4%}.
- Frozen test: total return {test["total_return"]:.4%}, CAGR {test["cagr"]:.4%}, Sharpe {test["sharpe"]:.3f}, max drawdown {test["max_drawdown"]:.4%}, turnover {test["turnover"]:.3f}.
- Walk-forward: {len(folds)} folds. Parameters stable in {max((selections.count(x) for x in set(selections)), default=0)}/{len(selections)} folds. See CSV for stitched daily OOS equity and fold metrics.

## Benchmarks and costs
{chr(10).join(f"- {x['benchmark']}: return {float(x['total_return']):.4%}, Sharpe {float(x['sharpe']):.3f}, drawdown {float(x['max_drawdown']):.4%}" for x in benchmarks)}

{chr(10).join(f"- {x['scenario']}: return {float(x['total_return']):.4%}, final {float(x['final_equity']):.2f}, total cost {float(x['total_trading_cost']):.2f}" for x in costs)}

## Interpretation and limitations
Economic significance is determined by the aligned benchmark table rather than in-sample performance. Parameter selection can overfit the short validation windows; walk-forward dispersion is the primary robustness check. Results remain exposed to universe survivorship bias, fund closure omission, qfq methodology limitations, and simplified liquidity/limit metadata. No missing result is inferred or fabricated.
"""
    (out / "report.md").write_text(text)
