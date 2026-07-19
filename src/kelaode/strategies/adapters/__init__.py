"""Safe templates for adapting external strategy logic without framework dependencies."""

from __future__ import annotations
from typing import Callable


class CallableSignalAdapter:
    def __init__(self, callable_: Callable):
        self.callable = callable_

    def signals(self, index, date, market, portfolio):
        return dict(self.callable(market, portfolio))


class DataFrameSignalAdapter:
    """Build a DataFrame from visible bars only, then read one supported output column."""

    def __init__(self, callable_, column="signal"):
        if column not in {"signal", "score", "target_weight"}:
            raise ValueError("unsupported output column")
        self.callable, self.column = callable_, column

    def values(self, market):
        import pandas as pd

        rows = []
        for symbol in market.available_symbols:
            for bar in market._data[symbol]:
                if bar.trade_date <= market.current_date:
                    rows.append(
                        {
                            "date": bar.trade_date,
                            "symbol": symbol,
                            "open": bar.open,
                            "high": bar.high,
                            "low": bar.low,
                            "close": bar.close,
                            "volume": bar.volume,
                        }
                    )
        result = self.callable(pd.DataFrame(rows).copy())
        if self.column not in result:
            raise ValueError(f"missing {self.column} column")
        latest = (
            result.sort_values(["date", "symbol"]).groupby("symbol", sort=True).tail(1)
        )
        return dict(zip(latest["symbol"], latest[self.column]))


class BacktraderLogicAdapter:
    """Template: calculate indicators from ``market.history`` and translate ``next`` to values."""


class FreqtradeLogicAdapter:
    """Template: run populate_indicators/entry/exit on the visible DataFrame slice only."""


class JoinQuantStyleAdapter:
    """Template lifecycle for initialize, before_trading_start and handle_data callbacks."""
