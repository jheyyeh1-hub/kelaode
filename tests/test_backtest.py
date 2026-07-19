from datetime import date, timedelta

import pytest

from kelaode.backtest import BacktestConfig, ETFBacktester, MovingAverageCrossStrategy
from kelaode.market_data import DailyBar


def make_bars(closes: list[float]) -> list[DailyBar]:
    return [
        DailyBar(date(2024, 1, 1) + timedelta(days=index), close, close, close, close, 1_000)
        for index, close in enumerate(closes)
    ]


def test_signal_executes_on_next_open_without_lookahead() -> None:
    result = ETFBacktester(
        BacktestConfig(initial_cash=1_000, minimum_commission=0, commission_rate=0, slippage_rate=0)
    ).run(make_bars([10, 11, 12, 9]), MovingAverageCrossStrategy(1, 2))

    # The bullish signal first observed at the second close is not allowed to
    # trade that close. At the third open a lot is unaffordable; the signal from
    # the third close can only fill at the cheaper fourth open.
    assert [(trade.trade_date, trade.side, trade.quantity) for trade in result.trades] == [
        (date(2024, 1, 4), "buy", 100)
    ]


def test_backtest_completes_buy_mark_to_market_sell_cycle() -> None:
    class AlternatingStrategy:
        def target_weight(self, index, bars):
            return [1.0, 0.0, 0.0][index]

    result = ETFBacktester(
        BacktestConfig(initial_cash=10_000, minimum_commission=5, commission_rate=0, slippage_rate=0)
    ).run(make_bars([10, 11, 12]), AlternatingStrategy())

    assert [(trade.side, trade.quantity) for trade in result.trades] == [("buy", 900), ("sell", 900)]
    assert result.equity_curve[-1].position == 0
    assert result.equity_curve[-1].cash == pytest.approx(10_890)
    assert result.total_return == pytest.approx(0.089)
    assert result.max_drawdown <= 0


def test_invalid_strategy_weight_is_rejected() -> None:
    class LeveragedStrategy:
        def target_weight(self, index, bars):
            return 1.1

    with pytest.raises(ValueError, match="between zero and one"):
        ETFBacktester().run(make_bars([10]), LeveragedStrategy())
