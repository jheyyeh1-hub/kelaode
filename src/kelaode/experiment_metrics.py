"""Defensive performance and benchmark statistics for experiment reports."""

from __future__ import annotations
from math import sqrt
from datetime import date
from statistics import mean, stdev
from typing import Mapping, Sequence


def _returns(values):
    return [b / a - 1 for a, b in zip(values, values[1:]) if a != 0]


def _std(x):
    return stdev(x) if len(x) > 1 else 0.0


def _safe(a, b):
    return a / b if b else 0.0


def performance_metrics(
    equity: Sequence[float], trades: Sequence[Mapping] = (), periods_per_year=252
) -> dict:
    if not equity:
        return {
            k: 0.0
            for k in (
                "total_return",
                "cagr",
                "annualized_volatility",
                "sharpe",
                "sortino",
                "calmar",
                "max_drawdown",
                "win_rate",
                "profit_factor",
                "turnover",
                "gross_exposure",
                "net_exposure",
                "trade_count",
                "average_holding_period",
                "average_trade_return",
            )
        }
    r = _returns(equity)
    total = equity[-1] / equity[0] - 1 if equity[0] else 0.0
    cagr = (
        (equity[-1] / equity[0]) ** (periods_per_year / max(1, len(r))) - 1
        if equity[0] > 0 and equity[-1] >= 0
        else -1.0
    )
    vol = _std(r) * sqrt(periods_per_year)
    sharpe = _safe(mean(r) * periods_per_year, vol) if r else 0.0
    downside = (
        sqrt(sum(min(x, 0) ** 2 for x in r) / len(r)) * sqrt(periods_per_year)
        if r
        else 0.0
    )
    peak = equity[0]
    dd = []
    for x in equity:
        peak = max(peak, x)
        dd.append(x / peak - 1 if peak else 0)
    maxdd = min(dd)
    duration = run = 0
    for x in dd:
        run = run + 1 if x < 0 else 0
        duration = max(duration, run)
    trade_returns = [float(t.get("return", 0)) for t in trades]
    wins = [x for x in trade_returns if x > 0]
    losses = [x for x in trade_returns if x < 0]
    notionals = sum(abs(float(t.get("notional", 0))) for t in trades)
    return {
        "total_return": total,
        "cagr": cagr,
        "annualized_volatility": vol,
        "sharpe": sharpe,
        "sortino": _safe(mean(r) * periods_per_year, downside) if r else 0.0,
        "calmar": _safe(cagr, abs(maxdd)),
        "max_drawdown": maxdd,
        "max_drawdown_duration": duration,
        "win_rate": _safe(len(wins), len(trade_returns)),
        "profit_factor": _safe(sum(wins), abs(sum(losses))),
        "turnover": _safe(notionals, mean(equity)),
        "gross_exposure": 0.0,
        "net_exposure": 0.0,
        "trade_count": len(trades),
        "average_holding_period": mean(
            [float(t.get("holding_period", 0)) for t in trades]
        )
        if trades
        else 0.0,
        "average_trade_return": mean(trade_returns) if trade_returns else 0.0,
    }


def execution_statistics(trades: Sequence[Mapping], initial_cash: float) -> dict:
    """Reconcile execution-level and realized long-only statistics.

    Realized returns are computed from an average-cost ledger including buy and
    sell commissions. Metrics that require a realized exit are explicitly null
    when no exit exists rather than silently reported as zero.
    """
    quantity: dict[str, int] = {}
    cost: dict[str, float] = {}
    realized: list[float] = []
    entry_ordinal: dict[str, float] = {}
    holding_periods: list[float] = []
    commissions = notional = 0.0
    for trade in trades:
        symbol, side = str(trade["symbol"]), str(trade["side"])
        trade_date = trade["date"]
        trade_date = trade_date if isinstance(trade_date, date) else date.fromisoformat(str(trade_date))
        qty, price, fee = int(trade["quantity"]), float(trade["price"]), float(trade["commission"])
        commissions += fee
        notional += qty * price
        held, basis = quantity.get(symbol, 0), cost.get(symbol, 0.0)
        if side == "buy":
            quantity[symbol] = held + qty
            cost[symbol] = basis + qty * price + fee
            entry_ordinal[symbol] = ((entry_ordinal.get(symbol, trade_date.toordinal()) * held
                                      + trade_date.toordinal() * qty) / (held + qty))
        elif side == "sell":
            if qty > held:
                raise ValueError("trade history oversells the long-only accounting ledger")
            allocated = basis * qty / held
            proceeds = qty * price - fee
            realized.append((proceeds - allocated) / allocated if allocated else 0.0)
            holding_periods.append(trade_date.toordinal() - entry_ordinal[symbol])
            quantity[symbol] = held - qty
            cost[symbol] = basis - allocated
        else:
            raise ValueError(f"unknown trade side: {side}")
    wins, losses = [value for value in realized if value > 0], [value for value in realized if value < 0]
    return {
        "execution_count": len(trades),
        "trade_count": len(trades),
        "realized_trade_count": len(realized),
        "win_rate": len(wins) / len(realized) if realized else None,
        "profit_factor": sum(wins) / abs(sum(losses)) if losses else None,
        "average_trade_return": mean(realized) if realized else None,
        "average_holding_period": mean(holding_periods) if holding_periods else None,
        "total_commissions": commissions,
        "traded_notional": notional,
        "turnover": notional / initial_cash,
        "final_positions": quantity,
    }


def align_benchmark(portfolio: Mapping, benchmark: Mapping):
    dates = sorted(set(portfolio) & set(benchmark))
    return dates, [portfolio[d] for d in dates], [benchmark[d] for d in dates]


def benchmark_metrics(
    portfolio: Mapping, benchmark: Mapping, periods_per_year=252
) -> dict:
    _, p, b = align_benchmark(portfolio, benchmark)
    if len(p) < 2:
        return {
            k: 0.0
            for k in (
                "excess_return",
                "tracking_error",
                "information_ratio",
                "beta",
                "alpha",
                "correlation",
                "active_drawdown",
            )
        }
    pr, br = _returns(p), _returns(b)
    active = [x - y for x, y in zip(pr, br)]
    tracking = _std(active) * sqrt(periods_per_year)
    bv = _std(br) ** 2
    covariance = (
        sum((x - mean(pr)) * (y - mean(br)) for x, y in zip(pr, br)) / (len(pr) - 1)
        if len(pr) > 1
        else 0
    )
    beta = _safe(covariance, bv)
    correlation = _safe(covariance, _std(pr) * _std(br))
    excess = p[-1] / p[0] - b[-1] / b[0]
    active_curve = [1.0]
    for x in active:
        active_curve.append(active_curve[-1] * (1 + x))
    peak = 1.0
    add = 0.0
    for x in active_curve:
        peak = max(peak, x)
        add = min(add, x / peak - 1)
    return {
        "excess_return": excess,
        "tracking_error": tracking,
        "information_ratio": _safe(mean(active) * periods_per_year, tracking),
        "beta": beta,
        "alpha": mean(pr) * periods_per_year - beta * mean(br) * periods_per_year,
        "correlation": correlation,
        "active_drawdown": add,
    }


def cost_scenarios(base: Mapping[str, float]) -> dict:
    """Canonical robustness scenarios, represented as deterministic overrides."""
    return {
        "base": dict(base),
        "double_commission": {
            **base,
            "commission_rate": 2 * base.get("commission_rate", 0),
        },
        "double_slippage": {**base, "slippage_rate": 2 * base.get("slippage_rate", 0)},
        "conservative_liquidity": {**base, "liquidity_multiplier": 0.5},
        "reduced_participation_rate": {
            **base,
            "participation_rate": base.get("participation_rate", 1) * 0.5,
        },
        "delayed_execution": {
            **base,
            "execution_delay": base.get("execution_delay", 0) + 1,
        },
    }
