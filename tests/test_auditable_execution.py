from datetime import date

import pytest

from kelaode import (
    AssetType, ConstraintEngine, ExcessVolumePolicy, FillModel, InstrumentMetadata,
    OrderIntent, OrderSide, OrderStatus, Position, ReasonCode, RejectedOrder,
    TradingConstraints, ValidatedOrder,
    PortfolioBacktestConfig, PortfolioBacktester,
)
from kelaode.market_data import DailyBar, MarketDataset


STOCK = InstrumentMetadata("600000.SH", AssetType.A_SHARE_STOCK, "SSE", t_plus_one=True,
                           stamp_duty_applicable=True)
ETF = InstrumentMetadata("510300.SH", AssetType.ETF, "SSE", t_plus_one=False)


def constraints(**changes):
    values = dict(available_cash=100_000, current_position=1_000, sellable_position=1_000,
                  cash_buffer=0, max_order_value=100_000)
    values.update(changes)
    return TradingConstraints(**values)


@pytest.mark.parametrize(("side", "changes", "code"), [
    (OrderSide.BUY, {"is_limit_up": True}, ReasonCode.LIMIT_UP_BUY),
    (OrderSide.SELL, {"is_limit_down": True}, ReasonCode.LIMIT_DOWN_SELL),
    (OrderSide.BUY, {"is_suspended": True}, ReasonCode.SUSPENDED),
    (OrderSide.BUY, {"has_market_data": False}, ReasonCode.NO_MARKET_DATA),
])
def test_market_constraints_have_stable_reason_codes(side, changes, code):
    result = ConstraintEngine().check(OrderIntent("x", side, 100, 10), constraints(**changes))
    assert isinstance(result, RejectedOrder)
    assert result.reason_code is code


def test_cash_buffer_weight_and_gross_constraints():
    engine = ConstraintEngine()
    cash = engine.check(OrderIntent("x", OrderSide.BUY, 100, 10),
                        constraints(available_cash=1_005, cash_buffer=10))
    assert isinstance(cash, RejectedOrder) and cash.reason_code is ReasonCode.INSUFFICIENT_CASH
    symbol = engine.check(OrderIntent("x", OrderSide.BUY, 200, 10),
                          constraints(account_equity=10_000, current_symbol_value=4_000,
                                      max_single_symbol_weight=.5))
    assert isinstance(symbol, RejectedOrder) and symbol.reason_code is ReasonCode.MAX_SYMBOL_WEIGHT
    gross = engine.check(OrderIntent("x", OrderSide.BUY, 200, 10),
                         constraints(account_equity=10_000, current_gross_exposure=9_000,
                                     max_gross_exposure=1))
    assert isinstance(gross, RejectedOrder) and gross.reason_code is ReasonCode.MAX_GROSS_EXPOSURE


def test_position_enforces_t_plus_one_sellable_quantity():
    position = Position(100, 100)
    position.start_day()
    position.apply(FillModel(minimum_commission=0).execute(
        ValidatedOrder("x", OrderSide.BUY, 100, 10, 0, "buy"),
        DailyBar(date(2024, 1, 2), 10, 10, 10, 10, 1_000), STOCK).fill)
    assert (position.total, position.sellable, position.today_bought) == (200, 100, 100)
    checked = ConstraintEngine().check(OrderIntent("x", OrderSide.SELL, 200, 10),
                                       constraints(current_position=200, sellable_position=position.sellable))
    assert not isinstance(checked, RejectedOrder)
    assert checked.quantity == 100


def test_partial_fill_volume_cap_and_directional_slippage_are_deterministic():
    model = FillModel(slippage_rate=.01, participation_rate=.1, minimum_commission=0,
                      excess_volume_policy=ExcessVolumePolicy.PARTIAL_FILL)
    bar = DailyBar(date(2024, 1, 2), 10, 10, 10, 10, 500)
    order = ValidatedOrder("x", OrderSide.BUY, 100, 10, 0, "id")
    first, second = model.execute(order, bar, ETF), model.execute(order, bar, ETF)
    assert first == second
    assert first.fill.quantity == 50
    assert first.fill.price == pytest.approx(10.1)
    assert first.fill.status is OrderStatus.PARTIALLY_FILLED
    assert first.unfilled_quantity == 50 and first.reason_code is ReasonCode.VOLUME_LIMIT


def test_stamp_duty_applies_only_to_stock_sells():
    model = FillModel(slippage_rate=0, minimum_commission=0, stamp_duty_rate=.001)
    bar = DailyBar(date(2024, 1, 2), 10, 10, 10, 10, 1_000)
    order = ValidatedOrder("x", OrderSide.SELL, 100, 10, 0, "id")
    assert model.execute(order, bar, ETF).fill.stamp_duty == 0
    assert model.execute(order, bar, STOCK).fill.stamp_duty == pytest.approx(1)


def test_optional_price_limits_remain_unknown_when_absent():
    bar = DailyBar(date(2024, 1, 2), 10, 10, 10, 10, 100)
    assert bar.previous_close is bar.upper_limit is bar.lower_limit is bar.suspended is None


def test_portfolio_execution_is_next_open_auditable_and_symbol_sorted():
    metadata = {
        "b": InstrumentMetadata("b", AssetType.ETF, "SSE", lot_size=1),
        "a": InstrumentMetadata("a", AssetType.ETF, "SSE", lot_size=1),
    }
    market = MarketDataset({symbol: [
        DailyBar(date(2024, 1, 1), 10, 10, 10, 10, 1_000),
        DailyBar(date(2024, 1, 2), 20, 20, 20, 20, 1_000),
    ] for symbol in metadata})

    class Strategy:
        def target_weights(self, trade_date, history):
            return {"b": .25, "a": .25}

    result = PortfolioBacktester(
        metadata, PortfolioBacktestConfig(initial_cash=1_000, minimum_commission=0),
        fill_model=FillModel(slippage_rate=0, minimum_commission=0, commission_rate=0),
    ).run(market, Strategy())
    second = result.daily_audits[1]
    assert [order.symbol for order in second.validated_orders] == ["a", "b"]
    assert [fill.price for fill in second.fills] == [20, 20]
    assert second.strategy_target == {"b": .25, "a": .25}
    assert second.end_of_day_positions == {"a": 12, "b": 12}
    assert second.cash == pytest.approx(520)
