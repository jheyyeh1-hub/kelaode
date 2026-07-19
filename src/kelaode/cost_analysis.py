"""Explicitly separate closed-loop stress from fixed-path accounting replay."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable

@dataclass(frozen=True)
class ReplayFill:
    symbol: str
    side: str
    quantity: int
    reference_price: float

def fixed_path_cost_replay(initial_cash: float, fills: Iterable[ReplayFill], *,
                           commission_rate: float, slippage_rate: float,
                           minimum_commission: float = 0.0) -> float:
    """Replay frozen quantities; returns terminal cash plus reference-valued inventory."""
    if min(commission_rate, slippage_rate, minimum_commission) < 0:
        raise ValueError("costs must be nonnegative")
    cash, positions, last = initial_cash, {}, {}
    for fill in fills:
        direction = 1 if fill.side == "buy" else -1 if fill.side == "sell" else 0
        if not direction or fill.quantity <= 0 or fill.reference_price <= 0:
            raise ValueError("invalid frozen fill path")
        execution = fill.reference_price * (1 + slippage_rate * direction)
        notional = execution * fill.quantity
        fee = max(minimum_commission, commission_rate * notional)
        cash -= direction * notional + fee
        positions[fill.symbol] = positions.get(fill.symbol, 0) + direction * fill.quantity
        last[fill.symbol] = fill.reference_price
    return cash + sum(quantity * last[symbol] for symbol, quantity in positions.items())

def closed_loop_cost_stress(run_backtest, scenarios):
    """Rerun the complete engine; affordability, rounding and holdings may change."""
    return {name: run_backtest(dict(costs)) for name, costs in scenarios.items()}
