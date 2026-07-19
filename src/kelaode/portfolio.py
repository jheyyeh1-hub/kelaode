"""Deterministic, long-only, multi-asset daily portfolio backtesting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import floor, isfinite, sqrt
from types import MappingProxyType
from typing import Mapping, Protocol, Sequence

from .market_data import DailyBar
from .core import (AssetType, ConstraintEngine, Fill, InstrumentMetadata, Order,
                   OrderIntent, OrderSide, ReasonCode, RejectedOrder,
                   TradingConstraints, ValidatedOrder)
from .execution import ExcessVolumePolicy, FillModel, Position


class PortfolioStrategy(Protocol):
    """Produce a complete target portfolio using information through today."""

    def target_weights(
        self, index: int, date: date, market: "MarketView", portfolio: "PortfolioSnapshot"
    ) -> Mapping[str, float]: ...


class _HoldTargets(dict[str, float]):
    """Internal marker used by baseline strategies when no rebalance is due."""


class FeeModel(Protocol):
    def execution_price(self, price: float, side: str) -> float: ...
    def commission(self, notional: float) -> float: ...


@dataclass(frozen=True)
class ETFFeeModel:
    commission_rate: float = 0.00025
    minimum_commission: float = 5.0
    slippage_rate: float = 0.0005

    def __post_init__(self) -> None:
        if min(self.commission_rate, self.minimum_commission, self.slippage_rate) < 0:
            raise ValueError("fee parameters cannot be negative")

    def execution_price(self, price: float, side: str) -> float:
        return price * (1 + self.slippage_rate if side == "buy" else 1 - self.slippage_rate)

    def commission(self, notional: float) -> float:
        return max(self.minimum_commission, notional * self.commission_rate)


@dataclass(frozen=True)
class PortfolioBacktestConfig:
    initial_cash: float = 100_000.0
    lot_size: int = 100
    cash_buffer: float = 0.0
    max_single_weight: float = 1.0
    max_gross_exposure: float = 1.0
    rebalance_tolerance: float = 0.0
    max_order_value: float = float("inf")
    commission_rate: float = 0.00025
    minimum_commission: float = 5.0
    slippage_rate: float = 0.0005
    participation_rate: float = 1.0
    partial_fill_policy: ExcessVolumePolicy = ExcessVolumePolicy.PARTIAL_FILL

    def __post_init__(self) -> None:
        if self.initial_cash <= 0 or self.lot_size <= 0:
            raise ValueError("initial_cash and lot_size must be positive")
        if not 0 <= self.cash_buffer < 1:
            raise ValueError("cash_buffer must be in [0, 1)")
        if not 0 < self.max_single_weight <= 1:
            raise ValueError("max_single_weight must be in (0, 1]")
        if not 0 < self.max_gross_exposure <= 1:
            raise ValueError("max_gross_exposure must be in (0, 1]")
        if self.rebalance_tolerance < 0:
            raise ValueError("rebalance_tolerance cannot be negative")
        if self.max_order_value <= 0:
            raise ValueError("max_order_value must be positive")
        if min(self.commission_rate, self.minimum_commission, self.slippage_rate) < 0:
            raise ValueError("cost parameters cannot be negative")
        if not 0 < self.participation_rate <= 1:
            raise ValueError("participation_rate must be in (0, 1]")

    @property
    def max_single_symbol_weight(self) -> float:
        """Explicit alias used by the shared constraint engine."""
        return self.max_single_weight


class MarketView:
    """Read-only point-in-time market data view with a hard no-lookahead boundary."""

    def __init__(self, data: Mapping[str, Sequence[DailyBar]], current_date: date) -> None:
        self._data = data
        self.current_date = current_date

    @property
    def available_symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self._data))

    @property
    def previous_date(self) -> date | None:
        """Previous observed market date, still bounded by ``current_date``."""
        dates = sorted({bar.trade_date for bars in self._data.values() for bar in bars
                        if bar.trade_date < self.current_date})
        return dates[-1] if dates else None

    def history(self, symbol: str, field: str, lookback: int) -> tuple[float, ...]:
        self._check_symbol(symbol)
        if field not in {"open", "high", "low", "close", "volume"}:
            raise ValueError(f"unknown market field: {field}")
        if lookback <= 0:
            raise ValueError("lookback must be positive")
        visible = [bar for bar in self._data[symbol] if bar.trade_date <= self.current_date]
        return tuple(float(getattr(bar, field)) for bar in visible[-lookback:])

    def latest(self, symbol: str) -> DailyBar | None:
        self._check_symbol(symbol)
        visible = [bar for bar in self._data[symbol] if bar.trade_date <= self.current_date]
        return visible[-1] if visible else None

    def is_tradable(self, symbol: str) -> bool:
        self._check_symbol(symbol)
        return any(bar.trade_date == self.current_date and isfinite(bar.open) and bar.open > 0
                   for bar in self._data[symbol])

    def returns(self, symbol: str, lookback: int) -> tuple[float, ...]:
        prices = self.history(symbol, "close", lookback + 1)
        return tuple(prices[i] / prices[i - 1] - 1 for i in range(1, len(prices))
                     if prices[i - 1] != 0)

    def rolling_window(self, symbol: str, fields: Sequence[str], lookback: int
                       ) -> Mapping[str, tuple[float, ...]]:
        return MappingProxyType({field: self.history(symbol, field, lookback) for field in fields})

    def cross_section(self, field: str) -> Mapping[str, float]:
        return MappingProxyType({s: float(getattr(bar, field)) for s in self.available_symbols
                                 if (bar := self.latest(s)) is not None})

    @property
    def missing_mask(self) -> Mapping[str, bool]:
        return MappingProxyType({s: self.latest(s) is None or
                                 self.latest(s).trade_date != self.current_date
                                 for s in self.available_symbols})

    def listing_age(self, symbol: str) -> int:
        self._check_symbol(symbol)
        return sum(bar.trade_date <= self.current_date for bar in self._data[symbol])

    @property
    def common_history_length(self) -> int:
        return min((self.listing_age(s) for s in self.available_symbols), default=0)

    def _check_symbol(self, symbol: str) -> None:
        if symbol not in self._data:
            raise ValueError(f"unknown symbol: {symbol}")


@dataclass(frozen=True)
class PortfolioSnapshot:
    cash: float
    positions: Mapping[str, int]
    average_cost: Mapping[str, float]
    market_value: float
    equity: float
    available_cash: float
    weights: Mapping[str, float]
    current_date: date


@dataclass(frozen=True)
class PortfolioTrade:
    trade_date: date
    symbol: str
    side: str
    quantity: int
    price: float
    commission: float


@dataclass(frozen=True)
class PortfolioOrder:
    trade_date: date
    signal_date: date
    symbol: str
    side: str
    requested_quantity: int
    filled_quantity: int
    status: str
    reason: str | None = None


@dataclass(frozen=True)
class Rejection:
    trade_date: date
    symbol: str
    reason: str
    reason_code: ReasonCode | None = None


@dataclass(frozen=True)
class DailyAudit:
    trade_date: date
    strategy_target: Mapping[str, float]
    generated_orders: tuple[Order, ...]
    validated_orders: tuple[ValidatedOrder, ...]
    rejected_orders: tuple[RejectedOrder, ...]
    fills: tuple[Fill, ...]
    end_of_day_positions: Mapping[str, int]
    cash: float
    equity: float
    constraint_reason_codes: tuple[ReasonCode, ...] = ()


@dataclass(frozen=True)
class PortfolioBacktestResult:
    equity_curve: Mapping[date, float]
    cash_curve: Mapping[date, float]
    positions_by_date: Mapping[date, Mapping[str, int]]
    weights_by_date: Mapping[date, Mapping[str, float]]
    trades: tuple[PortfolioTrade, ...]
    orders: tuple[PortfolioOrder, ...]
    rejections: tuple[Rejection, ...]
    turnover: float
    total_return: float
    annualized_return: float
    annualized_volatility: float
    max_drawdown: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    daily_audits: tuple[DailyAudit, ...]
    validated_orders: tuple[ValidatedOrder, ...]
    fills: tuple[Fill, ...]
    reason_codes: tuple[ReasonCode, ...]


class PortfolioBacktester:
    """Generate point-in-time targets and execute them through shared models next open."""

    def __init__(self, config: PortfolioBacktestConfig | None = None,
                 fee_model: FeeModel | None = None,
                 *, metadata: Mapping[str, InstrumentMetadata] | None = None,
                 constraint_engine: ConstraintEngine | None = None,
                 fill_model: FillModel | None = None) -> None:
        self.config = config or PortfolioBacktestConfig()
        self.fee_model = fee_model or ETFFeeModel(
            self.config.commission_rate, self.config.minimum_commission,
            self.config.slippage_rate)
        self.metadata = dict(metadata or {})
        self.constraint_engine = constraint_engine or ConstraintEngine()
        self.fill_model = fill_model or FillModel(
            slippage_rate=getattr(self.fee_model, "slippage_rate", self.config.slippage_rate),
            participation_rate=self.config.participation_rate,
            excess_volume_policy=self.config.partial_fill_policy,
            commission_rate=getattr(self.fee_model, "commission_rate", self.config.commission_rate),
            minimum_commission=getattr(self.fee_model, "minimum_commission", self.config.minimum_commission))

    def run(self, data: Mapping[str, Sequence[DailyBar]],
            strategy: PortfolioStrategy) -> PortfolioBacktestResult:
        normalized = self._normalize(data)
        dates = sorted({bar.trade_date for bars in normalized.values() for bar in bars})
        metadata = {symbol: self.metadata.get(symbol, InstrumentMetadata(
            symbol, AssetType.ETF, "UNKNOWN", lot_size=self.config.lot_size))
            for symbol in normalized}
        cash = self.config.initial_cash
        state = {symbol: Position() for symbol in normalized}
        average_cost = {symbol: 0.0 for symbol in normalized}
        last_close: dict[str, float] = {}
        pending: tuple[date, Mapping[str, float]] | None = None
        trades: list[PortfolioTrade] = []
        orders: list[PortfolioOrder] = []
        rejections: list[Rejection] = []
        audits: list[DailyAudit] = []
        all_validated: list[ValidatedOrder] = []
        all_fills: list[Fill] = []
        all_codes: list[ReasonCode] = []
        equities: dict[date, float] = {}
        cash_curve: dict[date, float] = {}
        position_curve: dict[date, Mapping[str, int]] = {}
        weight_curve: dict[date, Mapping[str, float]] = {}
        by_date = {symbol: {bar.trade_date: bar for bar in bars}
                   for symbol, bars in normalized.items()}
        serial = 0

        for index, today in enumerate(dates):
            for position in state.values():
                position.start_day()
            current = {symbol: bars.get(today) for symbol, bars in by_date.items()}
            generated: list[Order] = []
            validated: list[ValidatedOrder] = []
            rejected: list[RejectedOrder] = []
            fills: list[Fill] = []
            codes: list[ReasonCode] = []
            executing_target: Mapping[str, float] = MappingProxyType({})
            if pending is not None:
                executing_target = pending[1]
                cash, serial = self._rebalance(
                    today, pending[0], pending[1], current, last_close, cash, state,
                    average_cost, metadata, trades, orders, rejections, generated,
                    validated, rejected, fills, codes, serial)
            for symbol, bar in current.items():
                if bar is not None:
                    last_close[symbol] = bar.close
            positions = {symbol: position.total for symbol, position in state.items()}
            equity = cash + sum(positions[s] * last_close.get(s, 0.0) for s in positions)
            weights = {s: (positions[s] * last_close.get(s, 0.0) / equity if equity else 0.0)
                       for s in sorted(positions)}
            snapshot = PortfolioSnapshot(
                cash, MappingProxyType(dict(positions)), MappingProxyType(dict(average_cost)),
                equity - cash, equity, max(0.0, cash - equity * self.config.cash_buffer),
                MappingProxyType(weights), today)
            # The strategy receives only this point-in-time view, never MarketDataset.
            raw_target = strategy.target_weights(index, today, MarketView(normalized, today), snapshot)
            hold = isinstance(raw_target, _HoldTargets)
            target = dict(raw_target)
            self._validate_target(target, normalized)
            pending = None if hold else (today, MappingProxyType(target))
            equities[today] = equity
            cash_curve[today] = cash
            position_curve[today] = MappingProxyType(dict(positions))
            weight_curve[today] = MappingProxyType(weights)
            audit_target = MappingProxyType(dict(target if not hold else executing_target))
            audit = DailyAudit(today, audit_target, tuple(generated), tuple(validated),
                tuple(rejected), tuple(fills), position_curve[today], cash, equity, tuple(codes))
            audits.append(audit)
            all_validated.extend(validated); all_fills.extend(fills); all_codes.extend(codes)

        return self._result(dates, equities, cash_curve, position_curve, weight_curve,
                            trades, orders, rejections, audits, all_validated,
                            all_fills, all_codes)

    def _rebalance(self, today, signal_date, target, current, last_close, cash, state,
                   average_cost, metadata, trades, orders, rejections, generated,
                   validated, rejected, fills, codes, serial):
        open_marks = {s: (b.open if b is not None else last_close.get(s, 0.0))
                      for s, b in current.items()}
        equity = cash + sum(state[s].total * open_marks[s] for s in state)
        desired: dict[str, int] = {}
        intents: list[OrderIntent] = []
        for symbol in sorted(state):
            weight = target.get(symbol, 0.0)  # Complete target: omitted means zero.
            current_weight = state[symbol].total * open_marks[symbol] / equity if equity else 0
            if abs(current_weight - weight) <= self.config.rebalance_tolerance:
                desired[symbol] = state[symbol].total
                continue
            bar = current[symbol]
            if bar is None or not isfinite(bar.open) or bar.open <= 0:
                desired[symbol] = state[symbol].total
                serial += 1
                intent = OrderIntent(symbol, OrderSide.BUY, 1, 1.0,
                                     f"BT-{today:%Y%m%d}-{serial:06d}")
                order = Order(intent.client_order_id or "", intent)
                failure = RejectedOrder(intent, "no valid market data at execution open",
                                        ReasonCode.NO_MARKET_DATA, order.order_id)
                generated.append(order); rejected.append(failure); codes.append(failure.reason_code)
                rejections.append(Rejection(today, symbol, failure.reason, failure.reason_code))
                orders.append(PortfolioOrder(today, signal_date, symbol, "none", 0, 0,
                                             "rejected", failure.reason))
                continue
            lot = metadata[symbol].lot_size
            desired[symbol] = floor(equity * weight / bar.open / lot) * lot
            if desired[symbol] == state[symbol].total and abs(current_weight - weight) > 1e-12:
                rejections.append(Rejection(today, symbol,
                    "lot-size rounding prevented target adjustment", ReasonCode.LOT_SIZE))
        for symbol in sorted(desired):
            delta = desired[symbol] - state[symbol].total
            if delta:
                serial += 1
                intents.append(OrderIntent(symbol, OrderSide.BUY if delta > 0 else OrderSide.SELL,
                    abs(delta), current[symbol].open, f"BT-{today:%Y%m%d}-{serial:06d}"))
        intents.sort(key=lambda intent: (intent.side is OrderSide.BUY, intent.symbol))
        reserve = equity * self.config.cash_buffer
        for intent in intents:
            generated.append(Order(intent.client_order_id or "", intent))
            symbol = intent.symbol; bar = current[symbol]; position = state[symbol]
            gross = sum(item.total * open_marks[s] for s, item in state.items())
            checked = self.constraint_engine.check(intent, TradingConstraints(
                available_cash=cash, current_position=position.total,
                sellable_position=position.sellable, lot_size=metadata[symbol].lot_size,
                min_commission=self.fill_model.minimum_commission,
                commission_rate=self.fill_model.commission_rate, cash_buffer=reserve,
                max_order_value=self.config.max_order_value,
                max_single_symbol_weight=self.config.max_single_weight,
                max_gross_exposure=self.config.max_gross_exposure,
                account_equity=equity, current_symbol_value=position.total * bar.open,
                current_gross_exposure=gross, is_suspended=bar.suspended is True,
                is_limit_up=bar.upper_limit is not None and bar.open >= bar.upper_limit,
                is_limit_down=bar.lower_limit is not None and bar.open <= bar.lower_limit,
                shortable=metadata[symbol].shortable))
            if isinstance(checked, RejectedOrder):
                rejected.append(checked); codes.append(checked.reason_code)
                rejections.append(Rejection(today, symbol, checked.reason, checked.reason_code))
                orders.append(PortfolioOrder(today, signal_date, symbol, intent.side.value,
                                             intent.quantity, 0, "rejected", checked.reason))
                continue
            validated.append(checked)
            outcome = self.fill_model.execute(checked, bar, metadata[symbol])
            if outcome.reason_code is not None:
                codes.append(outcome.reason_code)
            if outcome.fill is None:
                reason = outcome.reason or "execution rejected"
                rejections.append(Rejection(today, symbol, reason, outcome.reason_code))
                orders.append(PortfolioOrder(today, signal_date, symbol, intent.side.value,
                                             intent.quantity, 0, "rejected", reason))
                continue
            fill = outcome.fill; fills.append(fill); old_qty = position.total
            position.apply(fill)
            total_fee = fill.commission + fill.stamp_duty
            if fill.side is OrderSide.BUY:
                cash -= fill.quantity * fill.price + total_fee
                average_cost[symbol] = (average_cost[symbol] * old_qty +
                    fill.quantity * fill.price + total_fee) / position.total
            else:
                cash += fill.quantity * fill.price - total_fee
                if position.total == 0:
                    average_cost[symbol] = 0.0
            trades.append(PortfolioTrade(today, symbol, fill.side.value, fill.quantity,
                                         fill.price, total_fee))
            reason = outcome.reason
            orders.append(PortfolioOrder(today, signal_date, symbol, fill.side.value,
                intent.quantity, fill.quantity, fill.status.value, reason))
            if outcome.unfilled_quantity:
                rejections.append(Rejection(today, symbol, reason or "volume constraint reduced order",
                                            ReasonCode.VOLUME_LIMIT))
        return cash, serial

    def _validate_target(self, target, data):
        for symbol, weight in target.items():
            if symbol not in data:
                raise ValueError(f"unknown symbol in target weights: {symbol}")
            if not isinstance(weight, (int, float)) or not isfinite(float(weight)):
                raise ValueError(f"target weight for {symbol} must be finite")
            if weight < 0:
                raise ValueError(f"target weight for {symbol} cannot be negative")
            if weight > self.config.max_single_weight:
                raise ValueError(f"target weight for {symbol} exceeds max_single_weight")
        if sum(target.values()) > self.config.max_gross_exposure + 1e-12:
            raise ValueError("target weight sum exceeds max_gross_exposure")

    @staticmethod
    def _normalize(data):
        if not data:
            raise ValueError("at least one symbol is required")
        result = {}
        for symbol, bars in data.items():
            if not isinstance(symbol, str) or not symbol:
                raise ValueError("symbols must be non-empty strings")
            ordered = tuple(sorted(bars, key=lambda b: b.trade_date))
            if not ordered:
                raise ValueError(f"no daily bars for symbol: {symbol}")
            if len({b.trade_date for b in ordered}) != len(ordered):
                raise ValueError(f"duplicate dates for symbol: {symbol}")
            result[symbol] = ordered
        return MappingProxyType(result)

    def _result(self, dates, equities, cash, positions, weights, trades, orders,
                rejections, audits, validated, fills, codes):
        values = list(equities.values())
        returns = [values[i] / values[i - 1] - 1 for i in range(1, len(values))]
        total = values[-1] / self.config.initial_cash - 1
        years = max((dates[-1] - dates[0]).days / 365.25, 1 / 252)
        annualized = (values[-1] / self.config.initial_cash) ** (1 / years) - 1
        volatility = self._std(returns) * sqrt(252)
        sharpe = 0.0 if volatility == 0 else (sum(returns) / len(returns)) * 252 / volatility
        downside = sqrt(sum(min(r, 0) ** 2 for r in returns) / len(returns)) * sqrt(252) if returns else 0
        sortino = 0.0 if downside == 0 else (sum(returns) / len(returns)) * 252 / downside
        drawdown = self._max_drawdown(values)
        turnover = sum(t.quantity * t.price for t in trades) / self.config.initial_cash
        return PortfolioBacktestResult(MappingProxyType(equities), MappingProxyType(cash),
            MappingProxyType(positions), MappingProxyType(weights), tuple(trades), tuple(orders),
            tuple(rejections), turnover, total, annualized, volatility, drawdown, sharpe, sortino,
            0.0 if drawdown == 0 else annualized / abs(drawdown), tuple(audits),
            tuple(validated), tuple(fills), tuple(codes))

    @staticmethod
    def _std(values):
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        return sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))

    @staticmethod
    def _max_drawdown(values):
        peak, worst = values[0], 0.0
        for value in values:
            peak = max(peak, value)
            worst = min(worst, value / peak - 1)
        return worst


@dataclass(frozen=True)
class EqualWeightBuyAndHold:
    symbols: Sequence[str]

    def target_weights(self, index, date, market, portfolio):
        if index != 0:
            return _HoldTargets()
        weight = 1 / len(self.symbols)
        return {symbol: weight for symbol in sorted(self.symbols)}


@dataclass(frozen=True)
class PeriodicEqualWeightRebalance:
    symbols: Sequence[str]
    every_n_days: int | None = None
    monthly: bool = True

    def __post_init__(self):
        if self.every_n_days is not None and self.every_n_days <= 0:
            raise ValueError("every_n_days must be positive")

    def target_weights(self, index, date, market, portfolio):
        rebalance = index == 0 or (self.every_n_days is not None and index % self.every_n_days == 0)
        if self.monthly and index > 0:
            # The first observed session in a calendar month triggers rebalancing.
            previous = market.previous_date
            rebalance |= previous is not None and previous.month != date.month
        if not rebalance:
            return _HoldTargets()
        weight = 1 / len(self.symbols)
        return {s: weight for s in sorted(self.symbols)}


@dataclass(frozen=True)
class CrossSectionalMomentumStrategy:
    symbols: Sequence[str]
    lookback: int = 20
    top_k: int = 1

    def __post_init__(self):
        if self.lookback <= 0 or not 0 < self.top_k <= len(self.symbols):
            raise ValueError("lookback and top_k must be positive and top_k cannot exceed symbols")

    def target_weights(self, index, date, market, portfolio):
        scores = []
        for symbol in sorted(self.symbols):
            closes = market.history(symbol, "close", self.lookback + 1)
            if len(closes) == self.lookback + 1:
                scores.append((closes[-1] / closes[0] - 1, symbol))
        selected = [symbol for _, symbol in sorted(scores, key=lambda x: (-x[0], x[1]))[:self.top_k]]
        return {symbol: 1 / len(selected) for symbol in selected} if selected else {}
