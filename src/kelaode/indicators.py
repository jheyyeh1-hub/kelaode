"""Small, point-in-time-safe technical indicator library.

Inputs are ordinary sequences and outputs are immutable tuples.  Warm-up periods and
windows containing non-finite values produce ``nan``; windows are always trailing.
"""

from __future__ import annotations

from math import isfinite
from statistics import mean, pstdev
from typing import Mapping, Sequence

NAN = float("nan")


def _roll(x, n, fn):
    if n <= 0:
        raise ValueError("window must be positive")
    out = []
    for i in range(len(x)):
        w = [float(v) for v in x[max(0, i - n + 1) : i + 1]]
        out.append(fn(w) if len(w) == n and all(isfinite(v) for v in w) else NAN)
    return tuple(out)


def sma(values: Sequence[float], window: int):
    return _roll(values, window, mean)


def rolling_maximum(values, window):
    return _roll(values, window, max)


def rolling_minimum(values, window):
    return _roll(values, window, min)


def rolling_volatility(values, window):
    return _roll(values, window, pstdev)


def rolling_zscore(values, window):
    def z(w):
        s = pstdev(w)
        return 0.0 if s == 0 else (w[-1] - mean(w)) / s

    return _roll(values, window, z)


def ema(values: Sequence[float], window: int):
    if window <= 0:
        raise ValueError("window must be positive")
    a = 2 / (window + 1)
    out = []
    last = NAN
    for value in values:
        v = float(value)
        last = (
            v
            if isfinite(v) and not isfinite(last)
            else (a * v + (1 - a) * last if isfinite(v) else NAN)
        )
        out.append(last)
    return tuple(out)


def momentum(values, window):
    x = tuple(map(float, values))
    return tuple(
        NAN
        if i < window or not all(map(isfinite, (x[i], x[i - window])))
        else x[i] - x[i - window]
        for i in range(len(x))
    )


def rate_of_change(values, window):
    x = tuple(map(float, values))
    return tuple(
        NAN
        if i < window
        or x[i - window] == 0
        or not all(map(isfinite, (x[i], x[i - window])))
        else x[i] / x[i - window] - 1
        for i in range(len(x))
    )


rolling_return = rate_of_change


def rsi(values, window=14):
    x = tuple(map(float, values))
    d = [NAN] + [x[i] - x[i - 1] for i in range(1, len(x))]

    def calc(w):
        gain = mean([max(v, 0) for v in w])
        loss = mean([max(-v, 0) for v in w])
        return 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)

    return _roll(d, window, calc)


def true_range(high, low, close):
    if not (len(high) == len(low) == len(close)):
        raise ValueError("lengths differ")
    out = []
    for i, (h, low_value) in enumerate(zip(high, low)):
        vals = [float(h) - float(low_value)]
        if i:
            vals += [
                abs(float(h) - float(close[i - 1])),
                abs(float(low_value) - float(close[i - 1])),
            ]
        out.append(max(vals) if all(map(isfinite, vals)) else NAN)
    return tuple(out)


def atr(high, low, close, window=14):
    return sma(true_range(high, low, close), window)


def macd(values, fast=12, slow=26, signal=9):
    f, s = ema(values, fast), ema(values, slow)
    line = tuple(a - b for a, b in zip(f, s))
    sig = ema(line, signal)
    return line, sig, tuple(a - b for a, b in zip(line, sig))


def bollinger_bands(values, window=20, num_std=2):
    m = sma(values, window)
    sd = rolling_volatility(values, window)
    return (
        tuple(a - num_std * b for a, b in zip(m, sd)),
        m,
        tuple(a + num_std * b for a, b in zip(m, sd)),
    )


def donchian_channel(high, low, window=20):
    return rolling_minimum(low, window), rolling_maximum(high, window)


def rolling_max_drawdown(values, window):
    def dd(w):
        return min((v / max(w[: i + 1]) - 1 for i, v in enumerate(w)))

    return _roll(values, window, dd)


def cross_sectional_rank(values: Mapping[str, float], ascending=True):
    valid = {k: float(v) for k, v in values.items() if isfinite(float(v))}
    ordered = sorted(valid, key=lambda k: (valid[k] if ascending else -valid[k], k))
    n = len(ordered)
    return {
        k: (
            sum(i + 1 for i, x in enumerate(ordered) if valid[x] == valid[k])
            / sum(1 for x in ordered if valid[x] == valid[k])
            / (n if n else 1)
        )
        for k in ordered
    }


def volatility_rank(series: Mapping[str, Sequence[float]], window: int):
    vals = {k: rolling_volatility(v, window)[-1] for k, v in series.items() if v}
    return cross_sectional_rank(vals)
