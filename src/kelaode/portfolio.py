"""Multi-asset daily portfolio simulation using the shared constraint engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from math import floor
from typing import Mapping, Protocol

from .core import (
    ConstraintEngine, InstrumentMetadata, Order, OrderIntent, OrderSide,
    OrderStatus, ReasonCode, RejectedOrder, TradingConstraints, ValidatedOrder,
)
from .execution import FillModel, Position
from .market_data import DailyBar, MarketDataset


class PortfolioStrategy(Protocol):
    def target_weights(self, trade_date: date, history: MarketDataset) -> Mapping[str, float]: ...


@dataclass(frozen=True)
class PortfolioBacktestConfig:
    initial_cash: float = 100_000.0
    cash_buffer: float = 0.0
    max_order_value: float = float("inf")
    max_single_symbol_weight: float = 1.0
    max_gross_exposure: float = 1.0
    commission_rate: float = 0.00025
    minimum_commission: float = 5.0


@dataclass(frozen=True)
class DailyAudit:
    trade_date: date
    strategy_target: Mapping[str, float]
    generated_orders: tuple[Order, ...]
    validated_orders: tuple[ValidatedOrder, ...]
    rejected_orders: tuple[RejectedOrder, ...]
    fills: tuple[object, ...]
    end_of_day_positions: Mapping[str, int]
    cash: float
    equity: float
    constraint_reason_codes: tuple[ReasonCode, ...] = ()


@dataclass(frozen=True)
class PortfolioBacktestResult:
    daily_audits: tuple[DailyAudit, ...]

    @property
    def fills(self) -> tuple[object, ...]:
        return tuple(fill for audit in self.daily_audits for fill in audit.fills)


class PortfolioBacktester:
    """Execute prior-close targets at next open in a reproducible order."""

    def __init__(self, metadata: Mapping[str, InstrumentMetadata],
                 config: PortfolioBacktestConfig | None = None,
                 constraints: ConstraintEngine | None = None,
                 fill_model: FillModel | None = None) -> None:
        self.metadata = dict(metadata)
        self.config = config or PortfolioBacktestConfig()
        self.constraints = constraints or ConstraintEngine()
        self.fill_model = fill_model or FillModel(
            commission_rate=self.config.commission_rate,
            minimum_commission=self.config.minimum_commission,
        )

    def run(self, market: MarketDataset, strategy: PortfolioStrategy) -> PortfolioBacktestResult:
        dates = market.all_dates
        cash = self.config.initial_cash
        positions = {symbol: Position() for symbol in self.metadata}
        pending: Mapping[str, float] | None = None
        audits: list[DailyAudit] = []
        serial = 0
        last_close: dict[str, float] = {}
        for day in dates:
            for position in positions.values():
                position.start_day()
            bars = market.on_date(day)
            generated: list[Order] = []
            validated: list[ValidatedOrder] = []
            rejected: list[RejectedOrder] = []
            fills: list[object] = []
            reason_codes: list[ReasonCode] = []
            if pending is not None:
                marks = {s: (bars[s].open if s in bars else last_close.get(s, 0.0)) for s in self.metadata}
                equity_open = cash + sum(positions[s].total * marks[s] for s in self.metadata)
                intents: list[OrderIntent] = []
                for symbol in sorted(set(self.metadata) | set(pending)):
                    serial += 1
                    order_id = f"BT-{day:%Y%m%d}-{serial:06d}"
                    if symbol not in self.metadata or symbol not in bars:
                        intent = OrderIntent(symbol, OrderSide.BUY, 1, 1.0, order_id)
                        generated.append(Order(order_id, intent))
                        rejected.append(RejectedOrder(intent, "market data is unavailable", ReasonCode.NO_MARKET_DATA, order_id))
                        reason_codes.append(ReasonCode.NO_MARKET_DATA)
                        continue
                    weight = float(pending.get(symbol, 0.0))
                    if weight < 0 or weight > 1:
                        raise ValueError("target weights must be between zero and one")
                    bar = bars[symbol]
                    target = floor(equity_open * weight / bar.open / self.metadata[symbol].lot_size) * self.metadata[symbol].lot_size
                    delta = target - positions[symbol].total
                    if delta:
                        intents.append(OrderIntent(symbol, OrderSide.BUY if delta > 0 else OrderSide.SELL,
                                                   abs(delta), bar.open, order_id))
                # Cash released by sells is predictably available to buys.
                intents.sort(key=lambda x: (x.side is OrderSide.BUY, x.symbol))
                for intent in intents:
                    generated.append(Order(intent.client_order_id or "", intent))
                    symbol, bar = intent.symbol, bars[intent.symbol]
                    gross = sum(positions[s].total * marks[s] for s in self.metadata)
                    checked = self.constraints.check(intent, TradingConstraints(
                        available_cash=cash, current_position=positions[symbol].total,
                        sellable_position=positions[symbol].sellable,
                        lot_size=self.metadata[symbol].lot_size,
                        min_commission=self.config.minimum_commission,
                        commission_rate=self.config.commission_rate,
                        cash_buffer=self.config.cash_buffer,
                        max_order_value=self.config.max_order_value,
                        max_single_symbol_weight=self.config.max_single_symbol_weight,
                        max_gross_exposure=self.config.max_gross_exposure,
                        account_equity=equity_open,
                        current_symbol_value=positions[symbol].total * bar.open,
                        current_gross_exposure=gross,
                        is_suspended=bar.suspended is True,
                        is_limit_up=(bar.upper_limit is not None and bar.open >= bar.upper_limit),
                        is_limit_down=(bar.lower_limit is not None and bar.open <= bar.lower_limit),
                        shortable=self.metadata[symbol].shortable,
                    ))
                    if isinstance(checked, RejectedOrder):
                        rejected.append(checked); reason_codes.append(checked.reason_code); continue
                    validated.append(checked)
                    outcome = self.fill_model.execute(checked, bar, self.metadata[symbol])
                    if outcome.fill is not None:
                        fill = outcome.fill; fills.append(fill); positions[symbol].apply(fill)
                        if fill.side is OrderSide.BUY:
                            cash -= fill.quantity * fill.price + fill.commission
                        else:
                            cash += fill.quantity * fill.price - fill.commission - fill.stamp_duty
                    if outcome.reason_code:
                        reason_codes.append(outcome.reason_code)
            for symbol, bar in bars.items():
                last_close[symbol] = bar.close
            equity = cash + sum(p.total * last_close.get(s, 0.0) for s, p in positions.items())
            target = dict(strategy.target_weights(day, market))
            audits.append(DailyAudit(day, target, tuple(generated), tuple(validated), tuple(rejected),
                                     tuple(fills), {s: p.total for s, p in sorted(positions.items())},
                                     cash, equity, tuple(reason_codes)))
            pending = target
        return PortfolioBacktestResult(tuple(audits))
