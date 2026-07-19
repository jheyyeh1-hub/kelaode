"""Configuration-driven construction of strategies for shared experiments."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

from .open_source_rotation import SITMomentumRotationStrategy, SITRotationParameters
from .portfolio import CrossSectionalMomentumStrategy, EqualWeightBuyAndHold, PortfolioStrategy
from .time_series_trend import TimeSeriesTrendParameters, TimeSeriesTrendStrategy

ParameterParser = Callable[[Mapping[str, Any]], Any]
StrategyFactory = Callable[[Sequence[str], Any], PortfolioStrategy]


@dataclass(frozen=True)
class StrategyRegistration:
    """A strategy constructor and its configuration parameter boundary."""

    factory: StrategyFactory
    parse_parameters: ParameterParser
    fit_applicable: bool = False

    def create(self, symbols: Sequence[str], raw_parameters: Mapping[str, Any]) -> PortfolioStrategy:
        return self.factory(tuple(symbols), self.parse_parameters(dict(raw_parameters)))


def _keyword_registration(strategy_type: type) -> StrategyRegistration:
    return StrategyRegistration(
        factory=lambda symbols, parameters: strategy_type(symbols=symbols, **parameters),
        parse_parameters=lambda parameters: dict(parameters),
    )


STRATEGY_REGISTRY: Mapping[str, StrategyRegistration] = MappingProxyType({
    "EqualWeightBuyAndHold": _keyword_registration(EqualWeightBuyAndHold),
    "CrossSectionalMomentumStrategy": _keyword_registration(CrossSectionalMomentumStrategy),
    "SITMomentumRotationStrategy": StrategyRegistration(
        factory=lambda symbols, parameters: SITMomentumRotationStrategy(symbols, parameters),
        parse_parameters=lambda parameters: SITRotationParameters(**parameters),
    ),
    "TimeSeriesTrendStrategy": StrategyRegistration(
        factory=lambda symbols, parameters: TimeSeriesTrendStrategy(symbols, parameters),
        parse_parameters=lambda parameters: TimeSeriesTrendParameters(**parameters),
    ),
})


def create_strategy(name: str, symbols: Sequence[str], parameters: Mapping[str, Any]) -> PortfolioStrategy:
    """Construct a registered strategy without runner-specific control flow."""
    try:
        registration = STRATEGY_REGISTRY[name]
    except KeyError as exc:
        raise ValueError(f"unregistered strategy_class: {name}") from exc
    return registration.create(symbols, parameters)


def require_no_fit_strategy(name: str) -> None:
    """Selection runners currently support only registered fixed-rule strategies."""
    try:
        registration = STRATEGY_REGISTRY[name]
    except KeyError as exc:
        raise ValueError(f"unregistered strategy_class: {name}") from exc
    # Factories in the current registry create no-fit PortfolioStrategy objects.
    # A future registration may explicitly expose fitting only with a complete
    # fitted-state artifact protocol.
    if getattr(registration, "fit_applicable", False):
        raise ValueError("schema-2.0 selection currently rejects fittable strategies")
