from datetime import date

import pytest

from kelaode import (
    AssetType, ConstraintEngine, ExcessVolumePolicy, FillModel, InstrumentMetadata,
    OrderIntent, OrderSide, OrderStatus, Position, ReasonCode, RejectedOrder,
    TradingConstraints, ValidatedOrder,
    PortfolioBacktestConfig, PortfolioBacktester,
)
from kelaode.market_data import DailyBar


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
    market = {symbol: [
        DailyBar(date(2024, 1, 1), 10, 10, 10, 10, 1_000),
        DailyBar(date(2024, 1, 2), 20, 20, 20, 20, 1_000),
    ] for symbol in metadata}

    class Strategy:
        def target_weights(self, index, trade_date, market_view, portfolio):
            return {"b": .25, "a": .25}

    result = PortfolioBacktester(
        PortfolioBacktestConfig(initial_cash=1_000, minimum_commission=0, lot_size=1),
        metadata=metadata,
        fill_model=FillModel(slippage_rate=0, minimum_commission=0, commission_rate=0),
    ).run(market, Strategy())
    second = result.daily_audits[1]
    assert [order.symbol for order in second.validated_orders] == ["a", "b"]
    assert [fill.price for fill in second.fills] == [20, 20]
    assert second.strategy_target == {"b": .25, "a": .25}
    assert second.end_of_day_positions == {"a": 12, "b": 12}
    assert second.cash == pytest.approx(520)


class Targets:
    def __init__(self, targets):
        self.targets = targets

    def target_weights(self, index, trade_date, market, portfolio):
        return self.targets[min(index, len(self.targets) - 1)]


def test_partial_fill_accounting_matches_audit_and_equity():
    data = {"A": [DailyBar(date(2024, 1, day), 10, 10, 10, 10, 50)
                  for day in (1, 2)]}
    config = PortfolioBacktestConfig(initial_cash=1_000, lot_size=1,
                                     commission_rate=0, minimum_commission=0,
                                     slippage_rate=0, participation_rate=.5)
    result = PortfolioBacktester(config).run(data, Targets([{"A": 1}, {}]))
    audit = result.daily_audits[1]
    assert audit.fills[0].quantity == 25
    assert audit.end_of_day_positions["A"] == 25
    assert audit.cash == 750
    assert audit.equity == 1_000 == result.equity_curve[date(2024, 1, 2)]
    assert audit.constraint_reason_codes == (ReasonCode.VOLUME_LIMIT,)


def test_t_plus_one_and_next_open_execution_are_compatible():
    data = {"S": [DailyBar(date(2024, 1, day), 10, 10, 10, 10, 1_000)
                  for day in (1, 2, 3)]}
    metadata = {"S": InstrumentMetadata("S", AssetType.A_SHARE_STOCK, "SSE",
                                         lot_size=1, t_plus_one=True)}
    config = PortfolioBacktestConfig(initial_cash=100, lot_size=1,
                                     commission_rate=0, minimum_commission=0,
                                     slippage_rate=0)
    result = PortfolioBacktester(config, metadata=metadata).run(
        data, Targets([{"S": 1}, {"S": 0}, {}]))
    assert [(fill.side, fill.quantity) for fill in result.fills] == [
        (OrderSide.BUY, 10), (OrderSide.SELL, 10)]
    assert result.daily_audits[1].end_of_day_positions["S"] == 10
    assert result.daily_audits[2].end_of_day_positions["S"] == 0


def test_integrated_stock_stamp_duty_differs_from_etf():
    data = {"X": [DailyBar(date(2024, 1, day), 10, 10, 10, 10, 1_000)
                  for day in (1, 2, 3)]}
    config = PortfolioBacktestConfig(initial_cash=1_000, lot_size=1,
                                     commission_rate=0, minimum_commission=0,
                                     slippage_rate=0)
    strategy = Targets([{"X": 1}, {"X": 0}, {}])
    etf = PortfolioBacktester(config, metadata={"X": InstrumentMetadata(
        "X", AssetType.ETF, "SSE", lot_size=1)}).run(data, strategy)
    stock = PortfolioBacktester(config, metadata={"X": InstrumentMetadata(
        "X", AssetType.A_SHARE_STOCK, "SSE", lot_size=1,
        stamp_duty_applicable=True)}).run(data, strategy)
    assert etf.cash_curve[date(2024, 1, 3)] == 1_000
    assert stock.cash_curve[date(2024, 1, 3)] == pytest.approx(999)
    assert stock.fills[-1].stamp_duty == pytest.approx(1)


def test_result_retains_full_metrics_and_execution_is_repeatable():
    data = {"A": [DailyBar(date(2024, 1, day), 10, 10, 10, 10, 1_000)
                  for day in (1, 2, 3)]}
    config = PortfolioBacktestConfig(initial_cash=1_000, lot_size=1,
                                     commission_rate=0, minimum_commission=0,
                                     slippage_rate=0, rebalance_tolerance=.01)
    first = PortfolioBacktester(config).run(data, Targets([{"A": .5}] * 3))
    second = PortfolioBacktester(config).run(data, Targets([{"A": .5}] * 3))
    fields = ("equity_curve", "cash_curve", "positions_by_date", "weights_by_date",
              "trades", "orders", "rejections", "turnover", "total_return",
              "annualized_return", "annualized_volatility", "max_drawdown",
              "sharpe_ratio", "sortino_ratio", "calmar_ratio", "daily_audits",
              "validated_orders", "fills", "reason_codes")
    assert all(hasattr(first, name) for name in fields)
    assert first == second
    assert len(first.fills) == 1  # tolerance prevents redundant rebalancing
