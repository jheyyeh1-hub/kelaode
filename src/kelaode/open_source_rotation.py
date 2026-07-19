"""Point-in-time adaptation of SIT's transparent ETF rotation strategy."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
from typing import Mapping, Sequence

from .portfolio import HoldTargets, MarketView, PortfolioSnapshot
from .strategy_sdk import EqualWeightTopK, PortfolioConstructor, VolatilityScaledWeight


@dataclass(frozen=True)
class SITRotationParameters:
    """Serializable parameters for :class:`SITMomentumRotationStrategy`."""

    momentum_lookback: int = 126
    top_k: int = 2
    trend_window: int | None = None
    volatility_lookback: int | None = None
    rebalance_frequency: str = "monthly"
    rebalance_interval: int = 21
    minimum_listing_age: int | None = None
    max_weight: float = 1.0

    def __post_init__(self) -> None:
        if self.momentum_lookback < 1 or self.top_k < 1:
            raise ValueError("momentum_lookback and top_k must be positive")
        if self.trend_window is not None and self.trend_window < 2:
            raise ValueError("trend_window must be at least two")
        if self.volatility_lookback is not None and self.volatility_lookback < 2:
            raise ValueError("volatility_lookback must be at least two")
        if self.rebalance_frequency not in {"daily", "weekly", "monthly", "interval"}:
            raise ValueError("unsupported rebalance_frequency")
        if self.rebalance_interval < 1 or not 0 < self.max_weight <= 1:
            raise ValueError("invalid rebalance_interval or max_weight")
        if self.minimum_listing_age is not None and self.minimum_listing_age < 1:
            raise ValueError("minimum_listing_age must be positive")


class SITMomentumRotationStrategy:
    """Rank trailing returns, optionally filter trend and inverse-volatility weight.

    Signals are formed at the current close from ``MarketView`` and the portfolio
    engine executes the complete target at the following open.
    """

    def __init__(self, symbols: Sequence[str], parameters: SITRotationParameters | None = None,
                 constructor: PortfolioConstructor | None = None) -> None:
        self.symbols = tuple(sorted(set(symbols)))
        if not self.symbols:
            raise ValueError("symbols cannot be empty")
        self.parameters = parameters or SITRotationParameters()
        self.constructor = constructor or EqualWeightTopK(self.parameters.top_k)

    def _eligible(self, market: MarketView) -> tuple[str, ...]:
        p = self.parameters
        required = p.minimum_listing_age or max(
            p.momentum_lookback + 1, p.trend_window or 0,
            (p.volatility_lookback or 0) + 1)
        return tuple(s for s in self.symbols if s in market.available_symbols
                     and market.is_tradable(s) and market.listing_age(s) >= required)

    def signals(self, index, date, market: MarketView, portfolio) -> Mapping[str, int]:
        scores = self.scores(index, date, market, portfolio)
        selected = sorted(scores, key=lambda symbol: (-scores[symbol], symbol))[
            : self.parameters.top_k]
        return {symbol: 1 for symbol in selected}

    def scores(self, index, date, market: MarketView, portfolio) -> Mapping[str, float]:
        p, scores = self.parameters, {}
        for symbol in self._eligible(market):
            prices = market.history(symbol, "close", p.momentum_lookback + 1)
            if len(prices) < p.momentum_lookback + 1 or prices[0] <= 0:
                continue
            if p.trend_window is not None:
                trend = market.history(symbol, "close", p.trend_window)
                if len(trend) < p.trend_window or prices[-1] <= sum(trend) / len(trend):
                    continue
            score = prices[-1] / prices[0]
            if isfinite(score):
                scores[symbol] = score
        return scores

    def _rebalance_due(self, index: int, date, market: MarketView) -> bool:
        frequency = self.parameters.rebalance_frequency
        previous = market.previous_date
        if index == 0 or frequency == "daily":
            return True
        if frequency == "interval":
            return index % self.parameters.rebalance_interval == 0
        if previous is None:
            return True
        if frequency == "weekly":
            return previous.isocalendar()[:2] != date.isocalendar()[:2]
        return (previous.year, previous.month) != (date.year, date.month)

    def target_weights(self, index: int, date, market: MarketView,
                       portfolio: PortfolioSnapshot) -> Mapping[str, float]:
        if not self._rebalance_due(index, date, market):
            return HoldTargets()
        scores = self.scores(index, date, market, portfolio)
        selected = dict(sorted(scores.items(), key=lambda item: (-item[1], item[0]))
                        [: self.parameters.top_k])
        if self.parameters.volatility_lookback is None:
            weights = self.constructor.construct(selected)
        else:
            volatilities = {}
            for symbol in selected:
                returns = market.returns(symbol, self.parameters.volatility_lookback)
                if len(returns) < self.parameters.volatility_lookback:
                    continue
                mean = sum(returns) / len(returns)
                variance = sum((value - mean) ** 2 for value in returns) / max(1, len(returns) - 1)
                if variance > 0:
                    volatilities[symbol] = sqrt(variance)
            weights = VolatilityScaledWeight().construct(volatilities)
        return {symbol: min(float(weight), self.parameters.max_weight)
                for symbol, weight in sorted(weights.items())}
