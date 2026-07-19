from dataclasses import dataclass
from math import isclose, isnan

from kelaode.indicators import (
    atr,
    bollinger_bands,
    cross_sectional_rank,
    donchian_channel,
    ema,
    macd,
    momentum,
    rate_of_change,
    rolling_max_drawdown,
    rolling_maximum,
    rolling_minimum,
    rolling_return,
    rolling_volatility,
    rolling_zscore,
    rsi,
    sma,
    true_range,
)
from kelaode.strategy_sdk import (
    CashBuffer,
    EqualWeightTopK,
    MaxWeightCap,
    SignalToWeightAdapter,
    TradableOnlyFilter,
    TurnoverLimit,
    parameters_json,
)


def test_trailing_indicators_and_short_history():
    x = [1, 2, 3, 4, 5]
    assert isnan(sma(x, 3)[1]) and sma(x, 3)[-1] == 4
    assert ema(x, 3)[-1] == 4.0625
    assert isclose(rate_of_change(x, 2)[-1], 2 / 3)
    assert rolling_return(x, 2) == rate_of_change(x, 2)
    assert momentum(x, 2)[-1] == 2
    assert rolling_maximum(x, 2)[-1] == 5 and rolling_minimum(x, 2)[-1] == 4
    assert rolling_volatility([1, 1], 2)[-1] == 0
    assert rolling_zscore([1, 1], 2)[-1] == 0
    assert rolling_max_drawdown([2, 1, 3], 3)[-1] == -0.5
    assert rsi(x, 2)[-1] == 100


def test_range_and_band_indicators():
    assert true_range([2, 3], [1, 2], [1.5, 2.5]) == (1, 1.5)
    assert atr([2, 3], [1, 2], [1.5, 2.5], 2)[-1] == 1.25
    low, mid, high = bollinger_bands([1, 2, 3], 3)
    assert mid[-1] == 2 and low[-1] < mid[-1] < high[-1]
    dc = donchian_channel([2, 3], [1, 2], 2)
    assert isnan(dc[0][0]) and isnan(dc[1][0]) and (dc[0][-1], dc[1][-1]) == (1, 3)
    line, signal, hist = macd([1, 2, 3], 2, 3, 2)
    assert len(line) == len(signal) == len(hist) == 3


def test_cross_section_rank_is_deterministic_with_ties():
    assert cross_sectional_rank({"b": 1, "a": 1, "c": 2}) == {
        "a": 0.5,
        "b": 0.5,
        "c": 1,
    }


def test_constructors_signals_caps_cash_turnover_and_tradability():
    assert EqualWeightTopK(1).construct({"b": 1, "a": 1}) == {"a": 1}
    assert SignalToWeightAdapter().convert({"a": "LONG", "b": "FLAT"}) == {"a": 1}
    assert MaxWeightCap(0.3).construct({"a": 0.8}) == {"a": 0.3}
    assert CashBuffer(0.1).construct({"a": 1}) == {"a": 0.9}
    assert TurnoverLimit(0.2).construct({"a": 1}, current_weights={"a": 0}) == {
        "a": 0.2
    }
    assert TradableOnlyFilter().construct(
        {"a": 0.5}, tradable={"a": False}, current_weights={"a": 0.2}
    ) == {"a": 0.2}


def test_stable_parameter_json():
    @dataclass(frozen=True)
    class P:
        z: int = 2
        a: int = 1

    assert parameters_json(P()) == '{"a":1,"z":2}'


def test_no_lookahead_prefix_invariance():
    prefix = sma([1, 2, 3], 2)
    assert sma([1, 2, 3, 999], 2)[:3] == prefix
