from __future__ import annotations
from dataclasses import dataclass
from statistics import pstdev
from ..strategy_sdk import EqualWeightTopK, ScoreProportionalWeight


def _due(index, frequency):
    return index % frequency == 0


@dataclass(frozen=True)
class CrossSectionalMomentumRotation:
    lookback: int = 20
    top_k: int = 3
    rebalance_frequency: int = 5

    def target_weights(self, index, date, market, portfolio):
        if not _due(index, self.rebalance_frequency):
            return dict(portfolio.weights)
        scores = {
            s: (p[-1] / p[0] - 1)
            for s in market.available_symbols
            if len(p := market.history(s, "close", self.lookback + 1))
            == self.lookback + 1
            and p[0] > 0
        }
        return EqualWeightTopK(self.top_k).construct(scores)


@dataclass(frozen=True)
class TrendFilteredMomentum(CrossSectionalMomentumRotation):
    trend_window: int = 200

    def target_weights(self, index, date, market, portfolio):
        if not _due(index, self.rebalance_frequency):
            return dict(portfolio.weights)
        scores = {}
        for s in market.available_symbols:
            p = market.history(s, "close", max(self.lookback + 1, self.trend_window))
            if (
                len(p) >= max(self.lookback + 1, self.trend_window)
                and p[-1] > sum(p[-self.trend_window :]) / self.trend_window
            ):
                scores[s] = p[-1] / p[-self.lookback - 1] - 1
        return EqualWeightTopK(self.top_k).construct(scores)


@dataclass(frozen=True)
class MultiAssetRiskDiversification:
    lookback: int = 20
    max_weight: float = 0.4
    cash_buffer: float = 0.05
    rebalance_frequency: int = 5

    def target_weights(self, index, date, market, portfolio):
        if not _due(index, self.rebalance_frequency):
            return dict(portfolio.weights)
        inv = {}
        for s in market.available_symbols:
            r = market.returns(s, self.lookback)
            if len(r) >= self.lookback and (v := pstdev(r)) > 0:
                inv[s] = 1 / v
        total = sum(inv.values())
        return (
            {
                s: min(self.max_weight, (1 - self.cash_buffer) * v / total)
                for s, v in inv.items()
            }
            if total
            else {}
        )


@dataclass(frozen=True)
class MultiFactorETFStrategy:
    lookback: int = 20
    trend_window: int = 50
    top_k: int = 3
    momentum_weight: float = 1
    volatility_weight: float = -1
    trend_weight: float = 1
    score_weighted: bool = False

    def target_weights(self, index, date, market, portfolio):
        raw = {}
        for s in market.available_symbols:
            p = market.history(s, "close", max(self.lookback + 1, self.trend_window))
            r = market.returns(s, self.lookback)
            if (
                len(p) >= max(self.lookback + 1, self.trend_window)
                and len(r) >= self.lookback
            ):
                raw[s] = (
                    p[-1] / p[-self.lookback - 1] - 1,
                    pstdev(r),
                    p[-1] / (sum(p[-self.trend_window :]) / self.trend_window) - 1,
                )
        if not raw:
            return {}
        cols = list(zip(*raw.values()))
        stats = [(sum(c) / len(c), pstdev(c)) for c in cols]
        scores = {
            s: sum(
                w * (x - m) / sd if sd else 0
                for x, (m, sd), w in zip(
                    v,
                    stats,
                    (self.momentum_weight, self.volatility_weight, self.trend_weight),
                )
            )
            for s, v in raw.items()
        }
        selected = dict(
            sorted(scores.items(), key=lambda x: (-x[1], x[0]))[: self.top_k]
        )
        return (
            ScoreProportionalWeight().construct(
                {k: max(0, v) for k, v in selected.items()}
            )
            if self.score_weighted
            else EqualWeightTopK(self.top_k).construct(selected)
        )


@dataclass(frozen=True)
class VolatilityTargetStrategy:
    base_weights: dict[str, float]
    lookback: int = 20
    target_volatility: float = 0.1
    annualization: int = 252

    def target_weights(self, index, date, market, portfolio):
        vols = []
        for s, w in self.base_weights.items():
            r = market.returns(s, self.lookback)
            if len(r) >= self.lookback:
                vols.append(abs(w) * pstdev(r) * self.annualization**0.5)
        exposure = (
            min(1.0, self.target_volatility / sum(vols)) if sum(vols) > 0 else 0.0
        )
        return {s: max(0, w) * exposure for s, w in sorted(self.base_weights.items())}
