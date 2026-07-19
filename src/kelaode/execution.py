"""Deterministic daily fill and position accounting models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import floor

from .core import Fill, InstrumentMetadata, OrderSide, OrderStatus, ReasonCode, ValidatedOrder
from .market_data import DailyBar


class ExcessVolumePolicy(StrEnum):
    PARTIAL_FILL = "partial_fill"
    REJECT_EXCESS = "reject_excess"


@dataclass(frozen=True)
class FillResult:
    fill: Fill | None
    unfilled_quantity: int
    reason_code: ReasonCode | None = None
    reason: str | None = None


@dataclass(frozen=True)
class FillModel:
    """Fill at the supplied day's open, with directional slippage and volume cap."""

    slippage_rate: float = 0.0005
    participation_rate: float = 1.0
    excess_volume_policy: ExcessVolumePolicy = ExcessVolumePolicy.PARTIAL_FILL
    commission_rate: float = 0.00025
    minimum_commission: float = 5.0
    stamp_duty_rate: float = 0.001

    def __post_init__(self) -> None:
        if self.slippage_rate < 0 or not 0 < self.participation_rate <= 1:
            raise ValueError("invalid slippage or participation rate")

    def execute(self, order: ValidatedOrder, bar: DailyBar, metadata: InstrumentMetadata) -> FillResult:
        cap = floor(bar.volume * self.participation_rate)
        quantity = min(order.quantity, cap)
        if quantity <= 0:
            return FillResult(None, order.quantity, ReasonCode.VOLUME_LIMIT, "daily volume cap is zero")
        price = bar.open * (1 + self.slippage_rate if order.side is OrderSide.BUY else 1 - self.slippage_rate)
        notional = quantity * price
        commission = max(self.minimum_commission, notional * self.commission_rate)
        stamp = (notional * self.stamp_duty_rate
                 if order.side is OrderSide.SELL and metadata.stamp_duty_applicable else 0.0)
        unfilled = order.quantity - quantity
        status = OrderStatus.PARTIALLY_FILLED if unfilled else OrderStatus.FILLED
        fill = Fill(order.order_id, order.symbol, order.side, quantity, price, commission, stamp, status)
        if unfilled:
            message = ("quantity above daily volume cap was left unfilled" if self.excess_volume_policy is ExcessVolumePolicy.PARTIAL_FILL
                       else "quantity above daily volume cap was rejected")
            return FillResult(fill, unfilled, ReasonCode.VOLUME_LIMIT, message)
        return FillResult(fill, 0)


# More generic name for clients that prefer it.
ExecutionModel = FillModel


@dataclass
class Position:
    total: int = 0
    sellable: int = 0
    today_bought: int = 0

    def start_day(self) -> None:
        self.sellable = self.total
        self.today_bought = 0

    def apply(self, fill: Fill) -> None:
        if fill.side is OrderSide.BUY:
            self.total += fill.quantity
            self.today_bought += fill.quantity
        else:
            if fill.quantity > self.sellable:
                raise ValueError("fill exceeds sellable position")
            self.total -= fill.quantity
            self.sellable -= fill.quantity
