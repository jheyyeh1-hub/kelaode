"""Core primitives for a small-capital, non-HFT mainland broker quant system.

The module intentionally keeps strategy, constraints, and execution separated so a
real broker adapter can be added without changing research code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import floor
from typing import Protocol
from uuid import uuid4


class OrderSide(StrEnum):
    """Supported order directions."""

    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class OrderIntent:
    """A strategy-level trading intent before market and account constraints."""

    symbol: str
    side: OrderSide
    quantity: int
    limit_price: float


@dataclass(frozen=True)
class TradingConstraints:
    """Hard constraints that must be checked before reaching a broker adapter."""

    available_cash: float
    current_position: int = 0
    lot_size: int = 100
    min_commission: float = 5.0
    commission_rate: float = 0.00025
    cash_buffer: float = 20.0
    max_order_value: float = 20_000.0
    is_suspended: bool = False
    is_limit_up: bool = False
    is_limit_down: bool = False
    can_sell_today: bool = True


@dataclass(frozen=True)
class CheckedOrder:
    """Order after constraint checks."""

    symbol: str
    side: OrderSide
    quantity: int
    limit_price: float
    estimated_cost: float
    client_order_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True)
class RejectedOrder:
    """Rejected order with an auditable reason."""

    intent: OrderIntent
    reason: str


class BrokerAdapter(Protocol):
    """Boundary implemented by paper trading, QMT, Ptrade, or broker APIs."""

    def submit_order(self, order: CheckedOrder) -> str:
        """Submit an already checked order and return a broker order id."""


class ConstraintEngine:
    """Converts raw strategy intents into exchange- and account-compliant orders."""

    def check(self, intent: OrderIntent, constraints: TradingConstraints) -> CheckedOrder | RejectedOrder:
        if intent.quantity <= 0:
            return RejectedOrder(intent, "quantity must be positive")
        if intent.limit_price <= 0:
            return RejectedOrder(intent, "limit price must be positive")
        if constraints.is_suspended:
            return RejectedOrder(intent, "symbol is suspended")
        if intent.side is OrderSide.BUY and constraints.is_limit_up:
            return RejectedOrder(intent, "refuse buying at limit-up")
        if intent.side is OrderSide.SELL and constraints.is_limit_down:
            return RejectedOrder(intent, "refuse selling at limit-down")
        if intent.side is OrderSide.SELL and not constraints.can_sell_today:
            return RejectedOrder(intent, "T+1 restriction: position is not sellable today")

        quantity = self._round_quantity(intent, constraints)
        if quantity <= 0:
            return RejectedOrder(intent, "quantity is below tradable lot size")

        notional = quantity * intent.limit_price
        if notional > constraints.max_order_value:
            quantity = floor(constraints.max_order_value / intent.limit_price / constraints.lot_size) * constraints.lot_size
            notional = quantity * intent.limit_price
        if quantity <= 0:
            return RejectedOrder(intent, "order value cap leaves no tradable quantity")

        if intent.side is OrderSide.BUY:
            estimated_cost = notional + self._commission(notional, constraints)
            if estimated_cost + constraints.cash_buffer > constraints.available_cash:
                quantity = self._max_affordable_quantity(intent.limit_price, constraints)
                if quantity <= 0:
                    return RejectedOrder(intent, "insufficient cash after commission and buffer")
                notional = quantity * intent.limit_price
                estimated_cost = notional + self._commission(notional, constraints)
        else:
            quantity = min(quantity, constraints.current_position)
            if quantity <= 0:
                return RejectedOrder(intent, "insufficient sellable position")
            estimated_cost = self._commission(quantity * intent.limit_price, constraints)

        return CheckedOrder(intent.symbol, intent.side, quantity, intent.limit_price, estimated_cost)

    def _round_quantity(self, intent: OrderIntent, constraints: TradingConstraints) -> int:
        if intent.side is OrderSide.BUY:
            return floor(intent.quantity / constraints.lot_size) * constraints.lot_size
        return intent.quantity

    def _max_affordable_quantity(self, price: float, constraints: TradingConstraints) -> int:
        cash_for_trade = max(0.0, constraints.available_cash - constraints.cash_buffer - constraints.min_commission)
        return floor(cash_for_trade / price / constraints.lot_size) * constraints.lot_size

    def _commission(self, notional: float, constraints: TradingConstraints) -> float:
        return max(constraints.min_commission, notional * constraints.commission_rate)


class PaperBrokerAdapter:
    """In-memory broker adapter for paper trading and tests."""

    def __init__(self) -> None:
        self.submitted_orders: list[CheckedOrder] = []

    def submit_order(self, order: CheckedOrder) -> str:
        self.submitted_orders.append(order)
        return f"PAPER-{len(self.submitted_orders):06d}"


class ExecutionEngine:
    """Runs constraint checks before forwarding orders to a broker adapter."""

    def __init__(self, broker: BrokerAdapter, constraints: ConstraintEngine | None = None) -> None:
        self.broker = broker
        self.constraints = constraints or ConstraintEngine()

    def execute(self, intent: OrderIntent, trading_constraints: TradingConstraints) -> str | RejectedOrder:
        checked = self.constraints.check(intent, trading_constraints)
        if isinstance(checked, RejectedOrder):
            return checked
        return self.broker.submit_order(checked)
