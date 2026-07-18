from kelaode import ConstraintEngine, ExecutionEngine, OrderIntent, OrderSide, PaperBrokerAdapter, TradingConstraints
from kelaode.core import RejectedOrder


def test_buy_order_is_rounded_to_a_share_lot() -> None:
    order = ConstraintEngine().check(
        OrderIntent("600000.SH", OrderSide.BUY, 250, 10.0),
        TradingConstraints(available_cash=10_000),
    )

    assert not isinstance(order, RejectedOrder)
    assert order.quantity == 200


def test_buy_order_respects_cash_buffer_and_commission() -> None:
    order = ConstraintEngine().check(
        OrderIntent("600000.SH", OrderSide.BUY, 1_000, 10.0),
        TradingConstraints(available_cash=1_050, cash_buffer=20, min_commission=5),
    )

    assert not isinstance(order, RejectedOrder)
    assert order.quantity == 100


def test_limit_up_buy_is_rejected() -> None:
    result = ConstraintEngine().check(
        OrderIntent("600000.SH", OrderSide.BUY, 100, 10.0),
        TradingConstraints(available_cash=10_000, is_limit_up=True),
    )

    assert isinstance(result, RejectedOrder)
    assert "limit-up" in result.reason


def test_t_plus_one_sell_restriction_is_rejected() -> None:
    result = ConstraintEngine().check(
        OrderIntent("600000.SH", OrderSide.SELL, 100, 10.0),
        TradingConstraints(available_cash=10_000, current_position=100, can_sell_today=False),
    )

    assert isinstance(result, RejectedOrder)
    assert "T+1" in result.reason


def test_execution_engine_submits_checked_order() -> None:
    broker = PaperBrokerAdapter()
    order_id = ExecutionEngine(broker).execute(
        OrderIntent("510300.SH", OrderSide.BUY, 100, 4.0),
        TradingConstraints(available_cash=1_000),
    )

    assert order_id == "PAPER-000001"
    assert len(broker.submitted_orders) == 1
