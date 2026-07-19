from datetime import date

import pytest

from kelaode.market_data import DailyBar
from kelaode.open_source_rotation import (
    SITMomentumRotationStrategy,
    SITRotationParameters,
)
from kelaode.portfolio import MarketView, PortfolioSnapshot
from kelaode.sit_audit import reference_metrics


def test_independent_reference_metrics():
    metrics = reference_metrics(
        [100, 110, 99],
        [
            {"quantity": 2, "price": 10, "commission": 1},
            {"quantity": 2, "price": 11, "commission": 1},
        ],
    )
    assert metrics["total_return"] == pytest.approx(-0.01)
    assert metrics["max_drawdown"] == pytest.approx(-0.1)
    assert metrics["total_commission"] == 2


def test_reference_cost_cannot_improve_same_equity_path():
    low = reference_metrics([100, 105], [])
    high = reference_metrics([100, 104], [])
    assert high["total_return"] < low["total_return"]


def test_future_price_mutation_does_not_change_current_target():
    days = [date(2024, 1, n) for n in range(1, 7)]
    bars = tuple(
        DailyBar(d, p, p, p, p, 1000) for d, p in zip(days, [1, 2, 3, 4, 5, 6])
    )
    changed = bars[:-1] + (DailyBar(days[-1], 600, 600, 600, 600, 1000),)
    strategy = SITMomentumRotationStrategy(
        ("A",),
        SITRotationParameters(
            momentum_lookback=2,
            top_k=1,
            minimum_listing_age=2,
            max_weight=1,
            rebalance_frequency="daily",
        ),
    )
    snapshot = PortfolioSnapshot(100, {}, {}, 0, 100, 100, {}, days[4])
    original = strategy.target_weights(
        4, days[4], MarketView({"A": bars}, days[4]), snapshot
    )
    mutated = strategy.target_weights(
        4, days[4], MarketView({"A": changed}, days[4]), snapshot
    )
    assert original == mutated
