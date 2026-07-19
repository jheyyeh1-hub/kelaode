"""Auditable order, constraint, and execution primitives.

The same :class:`ConstraintEngine` is deliberately usable by simulations and a
future broker adapter.  It does not contain broker connectivity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import floor, isfinite
from typing import Protocol


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(StrEnum):
    CREATED = "created"
    VALIDATED = "validated"
    REJECTED = "rejected"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"


class ReasonCode(StrEnum):
    INVALID_QUANTITY = "INVALID_QUANTITY"
    INVALID_PRICE = "INVALID_PRICE"
    NO_MARKET_DATA = "NO_MARKET_DATA"
    SUSPENDED = "SUSPENDED"
    LIMIT_UP_BUY = "LIMIT_UP_BUY"
    LIMIT_DOWN_SELL = "LIMIT_DOWN_SELL"
    PRICE_LIMIT_UNKNOWN = "PRICE_LIMIT_UNKNOWN"
    T_PLUS_ONE = "T_PLUS_ONE"
    LOT_SIZE = "LOT_SIZE"
    MAX_ORDER_VALUE = "MAX_ORDER_VALUE"
    INSUFFICIENT_CASH = "INSUFFICIENT_CASH"
    INSUFFICIENT_POSITION = "INSUFFICIENT_POSITION"
    SHORT_NOT_ALLOWED = "SHORT_NOT_ALLOWED"
    MAX_SYMBOL_WEIGHT = "MAX_SYMBOL_WEIGHT"
    MAX_GROSS_EXPOSURE = "MAX_GROSS_EXPOSURE"
    VOLUME_LIMIT = "VOLUME_LIMIT"


class AssetType(StrEnum):
    ETF = "ETF"
    A_SHARE_STOCK = "A_SHARE_STOCK"
    CASH = "CASH"


@dataclass(frozen=True)
class InstrumentMetadata:
    symbol: str
    asset_type: AssetType
    exchange: str
    lot_size: int = 100
    price_limit_rule: str | None = None
    t_plus_one: bool = False
    stamp_duty_applicable: bool = False
    shortable: bool = False
    currency: str = "CNY"

    def __post_init__(self) -> None:
        if not self.symbol or self.lot_size <= 0:
            raise ValueError("symbol and lot_size must be valid")


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: OrderSide
    quantity: int
    limit_price: float
    client_order_id: str | None = None


@dataclass(frozen=True)
class Order:
    order_id: str
    intent: OrderIntent
    status: OrderStatus = OrderStatus.CREATED


@dataclass(frozen=True)
class TradingConstraints:
    available_cash: float
    current_position: int = 0
    sellable_position: int | None = None
    lot_size: int = 100
    min_commission: float = 5.0
    commission_rate: float = 0.00025
    cash_buffer: float = 20.0
    max_order_value: float = 20_000.0
    max_single_symbol_weight: float = 1.0
    max_gross_exposure: float = 1.0
    account_equity: float | None = None
    current_symbol_value: float = 0.0
    current_gross_exposure: float = 0.0
    has_market_data: bool = True
    is_suspended: bool = False
    is_limit_up: bool | None = False
    is_limit_down: bool | None = False
    can_sell_today: bool = True
    shortable: bool = False


@dataclass(frozen=True)
class ValidatedOrder:
    symbol: str
    side: OrderSide
    quantity: int
    limit_price: float
    estimated_cost: float
    order_id: str = ""
    status: OrderStatus = OrderStatus.VALIDATED


# Backwards-compatible public name.
CheckedOrder = ValidatedOrder


@dataclass(frozen=True)
class RejectedOrder:
    intent: OrderIntent
    reason: str
    reason_code: ReasonCode = ReasonCode.INVALID_QUANTITY
    order_id: str = ""
    status: OrderStatus = OrderStatus.REJECTED


@dataclass(frozen=True)
class Fill:
    order_id: str
    symbol: str
    side: OrderSide
    quantity: int
    price: float
    commission: float
    stamp_duty: float = 0.0
    status: OrderStatus = OrderStatus.FILLED


class BrokerAdapter(Protocol):
    def submit_order(self, order: ValidatedOrder) -> str: ...


class ConstraintEngine:
    """Validate and, where safe, reduce an intent to a tradable quantity."""

    def check(self, intent: OrderIntent, constraints: TradingConstraints) -> ValidatedOrder | RejectedOrder:
        reject = lambda code, text: RejectedOrder(intent, text, code, intent.client_order_id or "")
        if intent.quantity <= 0:
            return reject(ReasonCode.INVALID_QUANTITY, "quantity must be positive")
        if not isfinite(intent.limit_price) or intent.limit_price <= 0:
            return reject(ReasonCode.INVALID_PRICE, "limit price must be finite and positive")
        if not constraints.has_market_data:
            return reject(ReasonCode.NO_MARKET_DATA, "market data is unavailable")
        if constraints.is_suspended:
            return reject(ReasonCode.SUSPENDED, "symbol is suspended")
        if intent.side is OrderSide.BUY and constraints.is_limit_up is True:
            return reject(ReasonCode.LIMIT_UP_BUY, "refuse buying at limit-up")
        if intent.side is OrderSide.SELL and constraints.is_limit_down is True:
            return reject(ReasonCode.LIMIT_DOWN_SELL, "refuse selling at limit-down")
        if intent.side is OrderSide.SELL and not constraints.can_sell_today:
            return reject(ReasonCode.T_PLUS_ONE, "T+1 restriction: position is not sellable today")

        quantity = (floor(intent.quantity / constraints.lot_size) * constraints.lot_size
                    if intent.side is OrderSide.BUY else intent.quantity)
        if quantity <= 0:
            return reject(ReasonCode.LOT_SIZE, "quantity is below tradable lot size")
        if quantity * intent.limit_price > constraints.max_order_value:
            quantity = floor(constraints.max_order_value / intent.limit_price / constraints.lot_size) * constraints.lot_size
        if quantity <= 0:
            return reject(ReasonCode.MAX_ORDER_VALUE, "order value cap leaves no tradable quantity")

        equity = constraints.account_equity
        if equity is not None and equity > 0:
            new_value = constraints.current_symbol_value + (quantity * intent.limit_price if intent.side is OrderSide.BUY else 0)
            if new_value / equity > constraints.max_single_symbol_weight + 1e-12:
                return reject(ReasonCode.MAX_SYMBOL_WEIGHT, "maximum single-symbol weight would be exceeded")
            new_gross = constraints.current_gross_exposure + (quantity * intent.limit_price if intent.side is OrderSide.BUY else 0)
            if new_gross / equity > constraints.max_gross_exposure + 1e-12:
                return reject(ReasonCode.MAX_GROSS_EXPOSURE, "maximum gross exposure would be exceeded")

        notional = quantity * intent.limit_price
        if intent.side is OrderSide.BUY:
            cost = notional + self._commission(notional, constraints)
            if cost + constraints.cash_buffer > constraints.available_cash:
                quantity = self._max_affordable_quantity(intent.limit_price, constraints)
                if quantity <= 0:
                    return reject(ReasonCode.INSUFFICIENT_CASH, "insufficient cash after commission and buffer")
                notional = quantity * intent.limit_price
                cost = notional + self._commission(notional, constraints)
        else:
            available = constraints.sellable_position
            if available is None:
                available = constraints.current_position if constraints.can_sell_today else 0
            if quantity > available and not constraints.shortable:
                quantity = available
            if quantity <= 0:
                code = ReasonCode.SHORT_NOT_ALLOWED if constraints.current_position <= 0 else ReasonCode.INSUFFICIENT_POSITION
                return reject(code, "insufficient sellable position; short selling is not allowed")
            cost = self._commission(quantity * intent.limit_price, constraints)
        return ValidatedOrder(intent.symbol, intent.side, quantity, intent.limit_price, cost,
                              intent.client_order_id or "")

    @staticmethod
    def _commission(notional: float, c: TradingConstraints) -> float:
        return max(c.min_commission, notional * c.commission_rate)

    def _max_affordable_quantity(self, price: float, c: TradingConstraints) -> int:
        available = max(0.0, c.available_cash - c.cash_buffer)
        quantity = floor(available / price / c.lot_size) * c.lot_size
        while quantity > 0 and quantity * price + self._commission(quantity * price, c) > available:
            quantity -= c.lot_size
        return quantity


class PaperBrokerAdapter:
    def __init__(self) -> None:
        self.submitted_orders: list[ValidatedOrder] = []

    def submit_order(self, order: ValidatedOrder) -> str:
        self.submitted_orders.append(order)
        return f"PAPER-{len(self.submitted_orders):06d}"


class ExecutionEngine:
    def __init__(self, broker: BrokerAdapter, constraints: ConstraintEngine | None = None) -> None:
        self.broker = broker
        self.constraints = constraints or ConstraintEngine()

    def execute(self, intent: OrderIntent, trading_constraints: TradingConstraints) -> str | RejectedOrder:
        checked = self.constraints.check(intent, trading_constraints)
        return checked if isinstance(checked, RejectedOrder) else self.broker.submit_order(checked)
