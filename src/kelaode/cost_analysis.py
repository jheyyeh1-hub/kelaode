"""Explicitly separate closed-loop stress from fixed-path accounting replay."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, Mapping

@dataclass(frozen=True)
class ReplayFill:
    symbol: str
    side: str
    quantity: int
    reference_price: float

def fixed_path_cost_replay(initial_cash: float, fills: Iterable[ReplayFill], *,
                           commission_rate: float, slippage_rate: float,
                           minimum_commission: float = 0.0,
                           final_prices: Mapping[str, float] | None = None) -> float:
    """Replay frozen quantities and mark remaining holdings at explicit frozen prices."""
    if min(commission_rate, slippage_rate, minimum_commission) < 0:
        raise ValueError("costs must be nonnegative")
    cash, positions = initial_cash, {}
    for fill in fills:
        direction = 1 if fill.side == "buy" else -1 if fill.side == "sell" else 0
        if not direction or fill.quantity <= 0 or fill.reference_price <= 0:
            raise ValueError("invalid frozen fill path")
        execution = fill.reference_price * (1 + slippage_rate * direction)
        notional = execution * fill.quantity
        fee = max(minimum_commission, commission_rate * notional)
        cash -= direction * notional + fee
        positions[fill.symbol] = positions.get(fill.symbol, 0) + direction * fill.quantity
    marks = dict(final_prices or {})
    missing = {symbol for symbol, quantity in positions.items() if quantity and symbol not in marks}
    if missing:
        raise ValueError(f"final prices are required for open positions: {sorted(missing)}")
    if any(price <= 0 for price in marks.values()):
        raise ValueError("final prices must be positive")
    return cash + sum(quantity * marks[symbol] for symbol, quantity in positions.items() if quantity)

def closed_loop_cost_stress(run_backtest, scenarios):
    """Rerun the complete engine; affordability, rounding and holdings may change."""
    return {name: run_backtest(dict(costs)) for name, costs in scenarios.items()}
