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
from .backtest import BacktestConfig, BacktestResult, ETFBacktester, MovingAverageCrossStrategy
from .market_data import (
    AKShareETFDownloader,
    DEFAULT_ETF_UNIVERSE,
    DailyBar,
    DatasetQuality,
    MarketDataset,
    read_daily_bars,
    validate_bars,
    write_daily_bars,
)
from .portfolio import (
    CrossSectionalMomentumStrategy,
    DailyAudit,
    EqualWeightBuyAndHold,
    ETFFeeModel,
    FeeModel,
    HoldTargets,
    MarketView,
    PeriodicEqualWeightRebalance,
    PortfolioBacktestConfig,
    PortfolioBacktestResult,
    PortfolioBacktester,
    PortfolioOrder,
    PortfolioSnapshot,
    PortfolioStrategy,
    PortfolioTrade,
    Rejection,
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
    "CrossSectionalMomentumStrategy",
    "EqualWeightBuyAndHold",
    "ETFFeeModel",
    "FeeModel",
    "HoldTargets",
    "MarketView",
    "PeriodicEqualWeightRebalance",
    "PortfolioBacktestConfig",
    "PortfolioBacktestResult",
    "PortfolioBacktester",
    "PortfolioOrder",
    "PortfolioSnapshot",
    "PortfolioStrategy",
    "PortfolioTrade",
    "Rejection",
    "SITMomentumRotationStrategy",
    "SITRotationParameters",
    "TimeSeriesTrendParameters",
    "TimeSeriesTrendStrategy",
]

# Strategy SDK public API.
from .strategy_sdk import (CashBuffer, EqualWeightBottomK, EqualWeightTopK, LongOnlyFilter,
    MaxWeightCap, RankWeight, ScoreProportionalWeight, SignalToWeightAdapter,
    TradableOnlyFilter, TurnoverLimit, VolatilityScaledWeight, parameters_json)
from .strategies import (CrossSectionalMomentumRotation, MultiAssetRiskDiversification,
    MultiFactorETFStrategy, TrendFilteredMomentum, VolatilityTargetStrategy)
from .open_source_rotation import SITMomentumRotationStrategy, SITRotationParameters
from .time_series_trend import TimeSeriesTrendParameters, TimeSeriesTrendStrategy
