"""Research prototype for a mainland China retail quant trading stack."""

from .core import (
    AssetType, ConstraintEngine, Fill, InstrumentMetadata, Order, OrderStatus,
    ExecutionEngine,
    OrderIntent,
    OrderSide,
    PaperBrokerAdapter,
    ReasonCode, RejectedOrder, ValidatedOrder,
    TradingConstraints,
)
from .execution import ExecutionModel, ExcessVolumePolicy, FillModel, Position
from .portfolio import (DailyAudit, PortfolioBacktestConfig, PortfolioBacktestResult,
                        PortfolioBacktester)
from .backtest import BacktestConfig, BacktestResult, ETFBacktester, MovingAverageCrossStrategy
from .market_data import (
    AKShareETFDownloader, DEFAULT_ETF_UNIVERSE, DailyBar, DatasetQuality,
    MarketDataset, read_daily_bars, validate_bars, write_daily_bars,
)

__all__ = [
    "ConstraintEngine",
    "AssetType", "Fill", "InstrumentMetadata", "Order", "OrderStatus",
    "ExecutionEngine",
    "OrderIntent",
    "OrderSide",
    "PaperBrokerAdapter",
    "TradingConstraints",
    "ReasonCode", "RejectedOrder", "ValidatedOrder", "ExecutionModel",
    "ExcessVolumePolicy", "FillModel", "Position", "DailyAudit",
    "PortfolioBacktestConfig", "PortfolioBacktestResult", "PortfolioBacktester",
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
