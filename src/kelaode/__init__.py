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
from .market_data import (
    AKShareETFDownloader, DEFAULT_ETF_UNIVERSE, DailyBar, DatasetQuality,
    MarketDataset, read_daily_bars, validate_bars, write_daily_bars,
)

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
    "DatasetQuality",
    "DEFAULT_ETF_UNIVERSE",
    "ETFBacktester",
    "MovingAverageCrossStrategy",
    "MarketDataset",
    "read_daily_bars",
    "validate_bars",
    "write_daily_bars",
]
