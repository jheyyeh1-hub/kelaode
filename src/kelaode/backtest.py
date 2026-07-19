"""A deterministic, long-only daily A-share ETF backtest loop."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import floor, sqrt
from typing import Protocol, Sequence

from .market_data import DailyBar


class TargetWeightStrategy(Protocol):
    """Return a target ETF weight using data through ``index`` only."""

    def target_weight(self, index: int, bars: Sequence[DailyBar]) -> float: ...


@dataclass(frozen=True)
class MovingAverageCrossStrategy:
    """Invest fully when the fast close average is above the slow average."""

    fast_window: int = 5
    slow_window: int = 20

    def __post_init__(self) -> None:
        if self.fast_window <= 0 or self.slow_window <= self.fast_window:
            raise ValueError("windows must satisfy 0 < fast_window < slow_window")

    def target_weight(self, index: int, bars: Sequence[DailyBar]) -> float:
        if index + 1 < self.slow_window:
            return 0.0
        closes = [bar.close for bar in bars[index + 1 - self.slow_window : index + 1]]
        fast = sum(closes[-self.fast_window :]) / self.fast_window
        slow = sum(closes) / self.slow_window
        return 1.0 if fast > slow else 0.0


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: float = 100_000.0
    commission_rate: float = 0.00025
    minimum_commission: float = 5.0
    slippage_rate: float = 0.0005
    lot_size: int = 100

    def __post_init__(self) -> None:
        if self.initial_cash <= 0 or self.lot_size <= 0:
            raise ValueError("initial_cash and lot_size must be positive")
        if min(self.commission_rate, self.minimum_commission, self.slippage_rate) < 0:
            raise ValueError("cost parameters cannot be negative")


@dataclass(frozen=True)
class Trade:
    trade_date: date
    side: str
    quantity: int
    price: float
    commission: float


@dataclass(frozen=True)
class EquityPoint:
    trade_date: date
    cash: float
    position: int
    close: float
    equity: float


@dataclass(frozen=True)
class BacktestResult:
    equity_curve: tuple[EquityPoint, ...]
    trades: tuple[Trade, ...]
    total_return: float
    annualized_return: float
    max_drawdown: float
    sharpe_ratio: float


class ETFBacktester:
    """Execute yesterday's signal at today's open and mark holdings at close."""

    def __init__(self, config: BacktestConfig | None = None) -> None:
        self.config = config or BacktestConfig()

    def run(self, bars: Sequence[DailyBar], strategy: TargetWeightStrategy) -> BacktestResult:
        ordered = tuple(sorted(bars, key=lambda bar: bar.trade_date))
        if not ordered:
            raise ValueError("at least one daily bar is required")
        if len({bar.trade_date for bar in ordered}) != len(ordered):
            raise ValueError("daily bars contain duplicate dates")

        cash, position = self.config.initial_cash, 0
        pending_weight: float | None = None
        trades: list[Trade] = []
        curve: list[EquityPoint] = []

        for index, bar in enumerate(ordered):
            if pending_weight is not None:
                cash, position, trade = self._rebalance(bar, pending_weight, cash, position)
                if trade is not None:
                    trades.append(trade)
            equity = cash + position * bar.close
            curve.append(EquityPoint(bar.trade_date, cash, position, bar.close, equity))
            pending_weight = float(strategy.target_weight(index, ordered))
            if not 0.0 <= pending_weight <= 1.0:
                raise ValueError("strategy target weight must be between zero and one")

        values = [point.equity for point in curve]
        returns = [values[index] / values[index - 1] - 1 for index in range(1, len(values))]
        total_return = values[-1] / self.config.initial_cash - 1
        years = max((ordered[-1].trade_date - ordered[0].trade_date).days / 365.25, 1 / 252)
        annualized = (values[-1] / self.config.initial_cash) ** (1 / years) - 1
        return BacktestResult(
            tuple(curve),
            tuple(trades),
            total_return,
            annualized,
            self._max_drawdown(values),
            self._sharpe(returns),
        )

    def _rebalance(
        self, bar: DailyBar, target_weight: float, cash: float, position: int
    ) -> tuple[float, int, Trade | None]:
        equity_at_open = cash + position * bar.open
        target = floor(equity_at_open * target_weight / bar.open / self.config.lot_size) * self.config.lot_size
        delta = target - position
        if delta == 0:
            return cash, position, None
        side = "buy" if delta > 0 else "sell"
        price = bar.open * (1 + self.config.slippage_rate if delta > 0 else 1 - self.config.slippage_rate)
        quantity = abs(delta)

        if delta > 0:
            quantity = self._affordable_quantity(quantity, price, cash)
            if quantity == 0:
                return cash, position, None
            commission = self._commission(quantity * price)
            cash -= quantity * price + commission
            position += quantity
        else:
            commission = self._commission(quantity * price)
            cash += quantity * price - commission
            position -= quantity
        return cash, position, Trade(bar.trade_date, side, quantity, price, commission)

    def _affordable_quantity(self, desired: int, price: float, cash: float) -> int:
        quantity = desired
        while quantity > 0 and quantity * price + self._commission(quantity * price) > cash:
            quantity -= self.config.lot_size
        return quantity

    def _commission(self, notional: float) -> float:
        return max(self.config.minimum_commission, notional * self.config.commission_rate)

    @staticmethod
    def _max_drawdown(values: Sequence[float]) -> float:
        peak, worst = values[0], 0.0
        for value in values:
            peak = max(peak, value)
            worst = min(worst, value / peak - 1)
        return worst

    @staticmethod
    def _sharpe(returns: Sequence[float]) -> float:
        if len(returns) < 2:
            return 0.0
        mean = sum(returns) / len(returns)
        variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
        return 0.0 if variance == 0 else mean / sqrt(variance) * sqrt(252)
