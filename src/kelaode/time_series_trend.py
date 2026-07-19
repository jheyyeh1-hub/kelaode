"""Long-only, point-in-time time-series momentum with volatility scaling."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import isfinite, sqrt
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .portfolio import HoldTargets, MarketView, PortfolioSnapshot

TRADING_DAYS_PER_YEAR = 252
MINIMUM_VOLATILITY_LOOKBACK = 2
VOLATILITY_FLOOR = 1e-12


@dataclass(frozen=True)
class TimeSeriesTrendParameters:
    """Validated, result-affecting parameters for the time-series trend rule."""

    trend_lookback: int
    volatility_lookback: int
    rebalance_frequency: int
    signal_buffer: float
    maximum_active_assets: int | None = None

    def __post_init__(self) -> None:
        for name in ("trend_lookback", "volatility_lookback", "rebalance_frequency"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.volatility_lookback < MINIMUM_VOLATILITY_LOOKBACK:
            raise ValueError(
                f"volatility_lookback must be at least {MINIMUM_VOLATILITY_LOOKBACK}"
            )
        if (isinstance(self.signal_buffer, bool)
                or not isinstance(self.signal_buffer, (int, float))
                or not isfinite(float(self.signal_buffer))
                or self.signal_buffer < 0):
            raise ValueError("signal_buffer must be a finite nonnegative number")
        cap = self.maximum_active_assets
        if cap is not None and (isinstance(cap, bool) or not isinstance(cap, int) or cap <= 0):
            raise ValueError("maximum_active_assets must be null or a positive integer")


class TimeSeriesTrendStrategy:
    """Classify each asset by its own trend and inverse-volatility weight positives.

    The first observed session on which any symbol has both required histories is
    a rebalance date.  Subsequent rebalances occur every ``rebalance_frequency``
    observed union-calendar sessions. Signals use the current completed close and
    are executed by :class:`PortfolioBacktester` at the following available open.
    """

    def __init__(self, symbols: Sequence[str], parameters: TimeSeriesTrendParameters) -> None:
        if not symbols or any(not isinstance(symbol, str) or not symbol for symbol in symbols):
            raise ValueError("symbols must contain non-empty strings")
        if len(set(symbols)) != len(symbols):
            raise ValueError("symbols must be unique")
        self.symbols = tuple(sorted(symbols))
        self.parameters = parameters
        if (parameters.maximum_active_assets is not None
                and parameters.maximum_active_assets > len(self.symbols)):
            raise ValueError("maximum_active_assets cannot exceed the universe size")
        self._diagnostics: Mapping[str, Any] = MappingProxyType({})

    @staticmethod
    def _sample_volatility(returns: Sequence[float]) -> float:
        mean = sum(returns) / len(returns)
        variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
        return sqrt(variance) * sqrt(TRADING_DAYS_PER_YEAR)

    def _first_eligible_index(self, market: MarketView) -> int | None:
        required = max(self.parameters.trend_lookback, self.parameters.volatility_lookback) + 1
        calendar = market.observed_dates
        candidates = []
        for symbol in self.symbols:
            dates = market.observation_dates(symbol)
            if len(dates) >= required:
                candidates.append(calendar.index(dates[required - 1]))
        return min(candidates) if candidates else None

    def _observations(self, market: MarketView) -> dict[str, dict[str, Any]]:
        p = self.parameters
        observations: dict[str, dict[str, Any]] = {}
        for symbol in self.symbols:
            item: dict[str, Any] = {
                "trend_signal": None, "realized_volatility": None,
                "eligibility_reason": "insufficient_trend_history",
                "raw_inverse_volatility": None, "normalized_target_weight": 0.0,
            }
            if not market.is_available(symbol):
                item["eligibility_reason"] = "current_bar_unavailable"
                observations[symbol] = item
                continue
            if market.latest(symbol).suspended is True:
                item["eligibility_reason"] = "current_bar_suspended"
                observations[symbol] = item
                continue
            closes = market.history(symbol, "close", p.trend_lookback + 1)
            if len(closes) != p.trend_lookback + 1 or closes[0] <= 0:
                observations[symbol] = item
                continue
            trend = closes[-1] / closes[0] - 1
            item["trend_signal"] = trend if isfinite(trend) else None
            if not isfinite(trend):
                item["eligibility_reason"] = "nonfinite_trend"
                observations[symbol] = item
                continue
            returns = market.returns(symbol, p.volatility_lookback)
            if len(returns) != p.volatility_lookback or not all(isfinite(x) for x in returns):
                item["eligibility_reason"] = "insufficient_volatility_history"
                observations[symbol] = item
                continue
            volatility = self._sample_volatility(returns)
            item["realized_volatility"] = volatility if isfinite(volatility) else None
            if not isfinite(volatility) or volatility <= VOLATILITY_FLOOR:
                item["eligibility_reason"] = "volatility_below_floor"
            # Decimal comparison preserves the configured strict boundary for
            # ordinary decimal closes (for example, 110 / 100 - 1 == 0.10)
            # rather than activating it because of binary floating-point noise.
            elif (Decimal(str(closes[-1])) / Decimal(str(closes[0])) - Decimal(1)
                  <= Decimal(str(p.signal_buffer))):
                item["eligibility_reason"] = "trend_not_above_buffer"
            else:
                item["eligibility_reason"] = "eligible"
                item["raw_inverse_volatility"] = 1.0 / volatility
            observations[symbol] = item
        return observations

    def diagnostics(self) -> Mapping[str, Any]:
        """Return the last point-in-time diagnostic record."""
        return self._diagnostics

    def target_weights(self, index: int, date, market: MarketView,
                       portfolio: PortfolioSnapshot) -> Mapping[str, float]:
        first = self._first_eligible_index(market)
        rebalance = first is not None and index >= first and (
            index - first) % self.parameters.rebalance_frequency == 0
        observations = self._observations(market)
        if not rebalance:
            self._diagnostics = MappingProxyType({
                "rebalance": False, "active_asset_count": 0,
                "target_concentration": 0.0, "symbols": observations,
            })
            return HoldTargets()

        eligible = [symbol for symbol in self.symbols
                    if observations[symbol]["eligibility_reason"] == "eligible"]
        eligible.sort(key=lambda symbol: (
            -abs(observations[symbol]["trend_signal"]),
            observations[symbol]["realized_volatility"], symbol))
        cap = self.parameters.maximum_active_assets
        selected = eligible if cap is None else eligible[:cap]
        for symbol in eligible[len(selected):]:
            observations[symbol]["eligibility_reason"] = "maximum_active_assets"
            observations[symbol]["raw_inverse_volatility"] = None
        raw_total = sum(observations[s]["raw_inverse_volatility"] for s in selected)
        targets = ({symbol: observations[symbol]["raw_inverse_volatility"] / raw_total
                    for symbol in selected} if raw_total else {})
        for symbol, weight in targets.items():
            observations[symbol]["normalized_target_weight"] = weight
        concentration = sum(weight * weight for weight in targets.values())
        self._diagnostics = MappingProxyType({
            "rebalance": True, "active_asset_count": len(targets),
            "target_concentration": concentration, "symbols": observations,
        })
        return dict(sorted(targets.items()))
