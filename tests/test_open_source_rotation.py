from dataclasses import asdict
from datetime import date, timedelta

import pytest

from kelaode import (HoldTargets, MarketView, PortfolioBacktestConfig,
                     PortfolioBacktester, SITMomentumRotationStrategy,
                     SITRotationParameters)
from kelaode.market_data import DailyBar
from kelaode.strategy_sdk import SignalToWeightAdapter, parameters_json


def bars(prices, start=date(2024, 1, 1), missing=()):
    return [DailyBar(start + timedelta(days=i), p, p, p, p, 1_000_000)
            for i, p in enumerate(prices) if i not in missing]


def view(data, offset):
    day = min(b.trade_date for values in data.values() for b in values) + timedelta(days=offset)
    return MarketView(data, day)


def strategy(**kwargs):
    return SITMomentumRotationStrategy(("A", "B", "NEW"), SITRotationParameters(**kwargs))


def test_original_six_month_rank_and_top_k_logic():
    data = {"A": bars([10, 11, 12]), "B": bars([10, 9, 15]), "NEW": bars([10, 10, 10])}
    target = strategy(momentum_lookback=2, top_k=2, rebalance_frequency="daily").target_weights(
        2, date(2024, 1, 3), view(data, 2), None)
    assert target == {"A": .5, "B": .5}


def test_market_view_boundary_prevents_future_signal():
    data = {"A": bars([10, 11, 100]), "B": bars([10, 12, 1]), "NEW": bars([10, 10, 10])}
    scores = strategy(momentum_lookback=1).scores(1, date(2024, 1, 2), view(data, 1), None)
    assert scores["B"] > scores["A"]


def test_parameters_are_dataclass_serializable_and_validated():
    params = SITRotationParameters(momentum_lookback=63, top_k=3, trend_window=100)
    assert parameters_json(params) == parameters_json(asdict(params))
    with pytest.raises(ValueError, match="top_k"):
        SITRotationParameters(top_k=0)


def test_trend_filter_and_listing_age():
    data = {"A": bars([10, 8, 9]), "B": bars([10, 11, 12]), "NEW": bars([20], date(2024, 1, 3))}
    scores = strategy(momentum_lookback=1, trend_window=3, minimum_listing_age=2).scores(
        2, date(2024, 1, 3), view(data, 2), None)
    assert set(scores) == {"B"}


def test_missing_current_bar_excludes_symbol_without_filling_history():
    data = {"A": bars([10, 11, 12], missing=(2,)), "B": bars([10, 10, 11]), "NEW": bars([10])}
    scores = strategy(momentum_lookback=1, minimum_listing_age=2).scores(
        2, date(2024, 1, 3), view(data, 2), None)
    assert set(scores) == {"B"}


def test_inverse_volatility_scaling_prefers_lower_volatility():
    data = {"A": bars([10, 10.1, 10.2, 10.3]), "B": bars([10, 12, 10, 13]), "NEW": bars([10])}
    target = strategy(momentum_lookback=1, top_k=2, volatility_lookback=3,
                      minimum_listing_age=4, rebalance_frequency="daily").target_weights(
        3, date(2024, 1, 4), view(data, 3), None)
    assert target["A"] > target["B"] and sum(target.values()) == pytest.approx(1)


def test_rebalance_frequencies_return_hold_marker():
    data = {"A": bars([10] * 10), "B": bars([10] * 10), "NEW": bars([10] * 10)}
    interval = strategy(momentum_lookback=1, rebalance_frequency="interval", rebalance_interval=3)
    assert isinstance(interval.target_weights(2, date(2024, 1, 3), view(data, 2), None), HoldTargets)
    assert not isinstance(interval.target_weights(3, date(2024, 1, 4), view(data, 3), None), HoldTargets)


def test_signal_adapter_output_is_long_only_and_normalized():
    data = {"A": bars([10, 11]), "B": bars([10, 9]), "NEW": bars([10])}
    signals = strategy(momentum_lookback=1, minimum_listing_age=2, top_k=1).signals(
        1, date(2024, 1, 2), view(data, 1), None)
    assert SignalToWeightAdapter().convert(signals) == {"A": 1.0}


def test_fixture_backtest_is_deterministic_and_executes_next_open_with_costs():
    data = {"A": bars([10, 11, 12, 13]), "B": bars([10, 9, 8, 7])}
    config = PortfolioBacktestConfig(initial_cash=100_000, lot_size=100,
                                     commission_rate=.001, minimum_commission=0,
                                     slippage_rate=.001)
    model = SITMomentumRotationStrategy(tuple(data), SITRotationParameters(
        momentum_lookback=1, top_k=1, minimum_listing_age=2, rebalance_frequency="interval",
        rebalance_interval=2))
    first = PortfolioBacktester(config).run(data, model)
    second = PortfolioBacktester(config).run(data, model)
    assert first.trades == second.trades
    assert first.trades[0].trade_date == date(2024, 1, 4)
    assert first.trades[0].price > 13
    assert first.total_return == pytest.approx(second.total_return)
