"""Research prototype for a mainland China retail quant trading stack."""

from .core import (
    ConstraintEngine,
    ExecutionEngine,
    OrderIntent,
    OrderSide,
    PaperBrokerAdapter,
    TradingConstraints,
)
from .backtest import BacktestConfig, BacktestResult, ETFBacktester, MovingAverageCrossStrategy
from .market_data import AKShareETFDownloader, DailyBar, read_daily_bars

__all__ = [
    "ConstraintEngine",
    "ExecutionEngine",
    "OrderIntent",
    "OrderSide",
    "PaperBrokerAdapter",
    "TradingConstraints",
    "AKShareETFDownloader",
    "BacktestConfig",
    "BacktestResult",
    "DailyBar",
    "ETFBacktester",
    "MovingAverageCrossStrategy",
    "read_daily_bars",
]
