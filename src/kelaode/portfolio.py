"""Deterministic, long-only, multi-asset daily portfolio backtesting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import floor, isfinite, sqrt
from types import MappingProxyType
from typing import Mapping, Protocol, Sequence

from .market_data import DailyBar


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


class PortfolioBacktester:
    """Generate targets after close, execute them at the next session's open."""

    def __init__(self, config: PortfolioBacktestConfig | None = None,
                 fee_model: FeeModel | None = None) -> None:
        self.config = config or PortfolioBacktestConfig()
        self.fee_model = fee_model or ETFFeeModel()

    def run(self, data: Mapping[str, Sequence[DailyBar]], strategy: PortfolioStrategy) -> PortfolioBacktestResult:
        normalized = self._normalize(data)
        dates = sorted({bar.trade_date for bars in normalized.values() for bar in bars})
        cash = self.config.initial_cash
        positions = {symbol: 0 for symbol in normalized}
        average_cost = {symbol: 0.0 for symbol in normalized}
        last_close: dict[str, float] = {}
        pending: tuple[date, Mapping[str, float]] | None = None
        trades: list[PortfolioTrade] = []
        orders: list[PortfolioOrder] = []
        rejections: list[Rejection] = []
        equities: dict[date, float] = {}
        cash_curve: dict[date, float] = {}
        position_curve: dict[date, Mapping[str, int]] = {}
        weight_curve: dict[date, Mapping[str, float]] = {}

        by_date = {symbol: {bar.trade_date: bar for bar in bars} for symbol, bars in normalized.items()}
        for index, today in enumerate(dates):
            current = {symbol: bars.get(today) for symbol, bars in by_date.items()}
            if pending is not None:
                cash = self._rebalance(today, pending[0], pending[1], current, last_close, cash,
                                       positions, average_cost, trades, orders, rejections)
            for symbol, bar in current.items():
                if bar is not None:
                    last_close[symbol] = bar.close
            equity = cash + sum(positions[s] * last_close.get(s, 0.0) for s in positions)
            weights = {s: (positions[s] * last_close.get(s, 0.0) / equity if equity else 0.0)
                       for s in sorted(positions)}
            snapshot = PortfolioSnapshot(cash, MappingProxyType(dict(positions)),
                MappingProxyType(dict(average_cost)), equity - cash, equity,
                max(0.0, cash - equity * self.config.cash_buffer), MappingProxyType(weights), today)
            raw_target = strategy.target_weights(index, today, MarketView(normalized, today), snapshot)
            hold = isinstance(raw_target, _HoldTargets)
            target = dict(raw_target)
            self._validate_target(target, normalized)
            pending = None if hold else (today, MappingProxyType(target))
            equities[today] = equity
            cash_curve[today] = cash
            position_curve[today] = MappingProxyType(dict(positions))
            weight_curve[today] = MappingProxyType(weights)

        return self._result(dates, equities, cash_curve, position_curve, weight_curve,
                            trades, orders, rejections)

    def _rebalance(self, today, signal_date, target, current, last_close, cash, positions,
                   average_cost, trades, orders, rejections):
        open_marks = {s: (b.open if b is not None else last_close.get(s, 0.0)) for s, b in current.items()}
        equity = cash + sum(positions[s] * open_marks[s] for s in positions)
        desired: dict[str, int] = {}
        for symbol in sorted(positions):
            weight = target.get(symbol, 0.0)  # Complete-target semantics: omitted means zero.
            current_weight = positions[symbol] * open_marks[symbol] / equity if equity else 0
            if abs(current_weight - weight) <= self.config.rebalance_tolerance:
                desired[symbol] = positions[symbol]
            elif current[symbol] is None or not isfinite(current[symbol].open) or current[symbol].open <= 0:
                desired[symbol] = positions[symbol]
                reason = "no valid market data at execution open"
                rejections.append(Rejection(today, symbol, reason))
                orders.append(PortfolioOrder(today, signal_date, symbol, "none", 0, 0, "rejected", reason))
            else:
                desired[symbol] = floor(equity * weight / current[symbol].open / self.config.lot_size) * self.config.lot_size
                if desired[symbol] == positions[symbol] and abs(current_weight - weight) > 1e-12:
                    reason = "lot-size rounding prevented target adjustment"
                    rejections.append(Rejection(today, symbol, reason))
                    orders.append(PortfolioOrder(today, signal_date, symbol, "none", 0, 0,
                                                 "rejected", reason))

        # Sells precede buys; alphabetical ordering makes fills independent of mapping order.
        for symbol in sorted(desired):
            if desired[symbol] < positions[symbol]:
                qty = positions[symbol] - desired[symbol]
                price = self.fee_model.execution_price(current[symbol].open, "sell")
                fee = self.fee_model.commission(qty * price)
                cash += qty * price - fee
                positions[symbol] -= qty
                if positions[symbol] == 0:
                    average_cost[symbol] = 0.0
                trades.append(PortfolioTrade(today, symbol, "sell", qty, price, fee))
                orders.append(PortfolioOrder(today, signal_date, symbol, "sell", qty, qty, "filled"))
        reserve = equity * self.config.cash_buffer
        for symbol in sorted(desired):
            if desired[symbol] <= positions[symbol]:
                continue
            requested = desired[symbol] - positions[symbol]
            price = self.fee_model.execution_price(current[symbol].open, "buy")
            qty = requested
            while qty > 0 and qty * price + self.fee_model.commission(qty * price) > cash - reserve:
                qty -= self.config.lot_size
            if qty <= 0:
                reason = "insufficient cash after fees and cash buffer"
                rejections.append(Rejection(today, symbol, reason))
                orders.append(PortfolioOrder(today, signal_date, symbol, "buy", requested, 0, "rejected", reason))
                continue
            fee = self.fee_model.commission(qty * price)
            old_qty = positions[symbol]
            average_cost[symbol] = (average_cost[symbol] * old_qty + qty * price + fee) / (old_qty + qty)
            cash -= qty * price + fee
            positions[symbol] += qty
            status = "filled" if qty == requested else "partially_filled"
            reason = None if qty == requested else "cash constraint reduced order"
            trades.append(PortfolioTrade(today, symbol, "buy", qty, price, fee))
            orders.append(PortfolioOrder(today, signal_date, symbol, "buy", requested, qty, status, reason))
            if reason:
                rejections.append(Rejection(today, symbol, reason))
        return cash

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

    def _result(self, dates, equities, cash, positions, weights, trades, orders, rejections):
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
            0.0 if drawdown == 0 else annualized / abs(drawdown))

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
