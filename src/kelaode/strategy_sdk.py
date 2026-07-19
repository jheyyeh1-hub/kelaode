"""Stable strategy protocols, parameter serialization and portfolio constructors."""

from __future__ import annotations
from dataclasses import asdict, is_dataclass
from json import dumps
from math import isfinite
from typing import Any, Mapping, Protocol


class TargetWeightStrategy(Protocol):
    def target_weights(self, index, date, market, portfolio) -> Mapping[str, float]: ...


class RankingStrategy(Protocol):
    def scores(self, index, date, market, portfolio) -> Mapping[str, float]: ...


class SignalStrategy(Protocol):
    def signals(
        self, index, date, market, portfolio
    ) -> Mapping[str, float | int | str]: ...


class PortfolioConstructor(Protocol):
    def construct(
        self, values: Mapping[str, float], **context: Any
    ) -> Mapping[str, float]: ...


def parameters_json(parameters: Any) -> str:
    value = asdict(parameters) if is_dataclass(parameters) else parameters
    return dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _valid(v):
    return isfinite(float(v))


def _limit(w, max_gross=1.0):
    x = {k: max(0.0, float(v)) for k, v in sorted(w.items()) if _valid(v)}
    gross = sum(x.values())
    scale = min(1.0, max_gross / gross) if gross else 1.0
    return {k: v * scale for k, v in x.items()}


class EqualWeightTopK:
    def __init__(self, k: int, max_gross_exposure=1.0):
        self.k, self.max_gross_exposure = k, max_gross_exposure

    def construct(self, values, **context):
        keys = sorted(
            (k for k, v in values.items() if _valid(v)),
            key=lambda k: (-float(values[k]), k),
        )[: self.k]
        return {k: self.max_gross_exposure / len(keys) for k in keys} if keys else {}


class EqualWeightBottomK(EqualWeightTopK):
    def construct(self, values, **context):
        return super().construct({k: -float(v) for k, v in values.items()}, **context)


class ScoreProportionalWeight:
    def __init__(self, max_gross_exposure=1.0):
        self.max_gross_exposure = max_gross_exposure

    def construct(self, values, **context):
        return _limit(
            {k: max(0, float(v)) for k, v in values.items()}, self.max_gross_exposure
        )


class RankWeight(ScoreProportionalWeight):
    def construct(self, values, **context):
        keys = sorted(values, key=lambda k: (float(values[k]), k))
        return _limit({k: i + 1 for i, k in enumerate(keys)}, self.max_gross_exposure)


class VolatilityScaledWeight(ScoreProportionalWeight):
    def construct(self, values, **context):
        return _limit(
            {k: 1 / float(v) for k, v in values.items() if _valid(v) and float(v) > 0},
            self.max_gross_exposure,
        )


class MaxWeightCap:
    def __init__(self, cap):
        self.cap = cap

    def construct(self, values, **context):
        return _limit(
            {k: min(float(v), self.cap) for k, v in values.items()},
            context.get("max_gross_exposure", 1.0),
        )


class CashBuffer:
    def __init__(self, buffer):
        self.buffer = buffer

    def construct(self, values, **context):
        return _limit(values, 1 - self.buffer)


class LongOnlyFilter:
    def construct(self, values, **context):
        return {
            k: float(v) for k, v in sorted(values.items()) if _valid(v) and float(v) > 0
        }


class TradableOnlyFilter:
    def __init__(self, preserve_existing=True):
        self.preserve_existing = preserve_existing

    def construct(self, values, **context):
        tradable = context.get("tradable", {})
        current = context.get("current_weights", {})
        return {
            k: (
                float(v)
                if tradable.get(k, False)
                else float(current.get(k, 0))
                if self.preserve_existing
                else 0.0
            )
            for k, v in sorted(values.items())
        }


class TurnoverLimit:
    def __init__(self, limit):
        self.limit = limit

    def construct(self, values, **context):
        old = context.get("current_weights", {})
        keys = sorted(set(values) | set(old))
        turn = sum(abs(float(values.get(k, 0)) - float(old.get(k, 0))) for k in keys)
        a = min(1.0, self.limit / turn) if turn else 1.0
        return {
            k: float(old.get(k, 0))
            + a * (float(values.get(k, 0)) - float(old.get(k, 0)))
            for k in keys
        }


class SignalToWeightAdapter:
    def __init__(self, constructor=None):
        self.constructor = constructor or EqualWeightTopK(10**9)

    def convert(self, signals):
        v = {
            k: (
                1.0
                if str(x).upper() == "LONG"
                else 0.0
                if str(x).upper() == "FLAT"
                else max(0.0, float(x))
            )
            for k, x in signals.items()
        }
        return self.constructor.construct({k: x for k, x in v.items() if x > 0})
