from datetime import date, timedelta

import pytest

from kelaode import (
    CrossSectionalMomentumStrategy, ETFFeeModel, MarketView,
    PeriodicEqualWeightRebalance, PortfolioBacktestConfig, PortfolioBacktester,
)
from kelaode.market_data import DailyBar


def bars(prices, start=date(2024, 1, 1)):
    result = []
    for i, value in enumerate(prices):
        if isinstance(value, tuple):
            opening, close = value
        else:
            opening = close = value
        result.append(DailyBar(start + timedelta(days=i), opening, max(opening, close),
                               min(opening, close), close, 1000))
    return result


class SequenceStrategy:
    def __init__(self, values): self.values = values
    def target_weights(self, index, date, market, portfolio):
        return self.values[min(index, len(self.values) - 1)]


def engine(cash=10_000, lot=100, fee=None, **kwargs):
    return PortfolioBacktester(PortfolioBacktestConfig(initial_cash=cash, lot_size=lot, **kwargs),
                               fee or ETFFeeModel(0, 0, 0))


def test_two_assets_equal_weight_and_next_open_execution():
    data = {"B": bars([(10, 10), (20, 20)]), "A": bars([(10, 10), (10, 10)])}
    result = engine().run(data, SequenceStrategy([{"A": .5, "B": .5}, {}]))
    assert [(t.trade_date, t.symbol, t.quantity) for t in result.trades] == [
        (date(2024, 1, 2), "A", 500), (date(2024, 1, 2), "B", 200)]
    assert result.positions_by_date[date(2024, 1, 2)] == {"A": 500, "B": 200}


def test_sell_before_buy_and_mapping_order_determinism():
    signals = [{"A": 1}, {"B": 1}, {}]
    data1 = {"B": bars([10, 10, 10]), "A": bars([10, 10, 10])}
    data2 = {"A": bars([10, 10, 10]), "B": bars([10, 10, 10])}
    one = engine().run(data1, SequenceStrategy(signals))
    two = engine().run(data2, SequenceStrategy(signals))
    assert [(t.symbol, t.side, t.quantity) for t in one.trades] == [("A", "buy", 1000), ("A", "sell", 1000), ("B", "buy", 1000)]
    assert one.trades == two.trades and dict(one.equity_curve) == dict(two.equity_curve)


def test_minimum_commission_lot_rounding_and_cash_shortfall():
    result = engine(cash=1005, fee=ETFFeeModel(0, 5, 0)).run(
        {"A": bars([10, 10])}, SequenceStrategy([{"A": 1}, {}]))
    assert result.trades[0].quantity == 100
    assert result.trades[0].commission == 5
    tight = engine(cash=1004, fee=ETFFeeModel(0, 5, 0)).run(
        {"A": bars([10, 10])}, SequenceStrategy([{"A": 1}, {}]))
    assert not tight.trades
    assert "insufficient cash" in tight.rejections[0].reason


def test_missing_quote_is_not_filled_with_stale_open():
    data = {"A": [bars([10])[0], bars([10], date(2024, 1, 3))[0]], "B": bars([10, 10, 10])}
    result = engine().run(data, SequenceStrategy([{"A": 1}, {"A": 1}, {}]))
    assert not [t for t in result.trades if t.trade_date == date(2024, 1, 2)]
    assert any(r.trade_date == date(2024, 1, 2) and r.symbol == "A" for r in result.rejections)


@pytest.mark.parametrize("target, message", [
    ({"A": -.1}, "negative"), ({"A": .6, "B": .5}, "max_gross"),
    ({"NOPE": 1}, "unknown symbol"), ({"A": float("nan")}, "finite"),
])
def test_invalid_targets_are_clear(target, message):
    with pytest.raises(ValueError, match=message):
        engine().run({"A": bars([10]), "B": bars([10])}, SequenceStrategy([target]))


def test_market_view_cannot_see_future():
    view = MarketView({"A": bars([10, 20, 30])}, date(2024, 1, 2))
    assert view.history("A", "close", 99) == (10, 20)
    assert view.latest("A").close == 20
    assert view.current_date == date(2024, 1, 2)


def test_monthly_rebalance_and_momentum_use_only_history():
    start = date(2024, 1, 30)
    data = {"A": bars([10, 10, 12, 12], start), "B": bars([10, 10, 9, 9], start)}
    monthly = engine().run(data, PeriodicEqualWeightRebalance(("A", "B")))
    assert any(o.signal_date == date(2024, 2, 1) for o in monthly.orders)
    momentum_data = {"A": bars([10, 9, 12, 12], start), "B": bars([10, 10, 9, 9], start)}
    momentum = engine().run(momentum_data, CrossSectionalMomentumStrategy(("A", "B"), lookback=1, top_k=1))
    # Jan 31 selects B. The new A winner is only known at Feb 1 close and buys Feb 2.
    assert [(t.trade_date, t.symbol, t.side) for t in momentum.trades] == [
        (date(2024, 2, 1), "B", "buy"), (date(2024, 2, 2), "B", "sell"),
        (date(2024, 2, 2), "A", "buy")]
