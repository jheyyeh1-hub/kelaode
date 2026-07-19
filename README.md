# 中国大陆券商小资金非高频量化系统研究原型

本仓库提供一个从零构建、面向中国大陆券商实盘接入的小资金、非高频量化系统蓝图与可运行核心原型。目标不是追求毫秒级交易，而是在合法合规、可审计、可风控的前提下，逐步完成从研究、回测、仿真到实盘执行的闭环。

## 适用边界

- **市场**：A 股普通股票、ETF、可转债等 T+1/T+0 规则不同的品种需要分别建模。
- **资金规模**：小资金账户，默认强调佣金、最低收费、冲击成本、滑点和仓位集中度控制。
- **频率**：分钟级到日频，不做高频抢单，不依赖交易所直连。
- **券商接入**：通过可审计适配器对接券商柜台、官方/半官方 API、QMT/Ptrade 等终端能力；严禁绕过监管和券商风控。

## 核心模块

1. **策略层**：只输出目标仓位或交易意图，不直接下单。
2. **约束层**：处理涨跌停、停牌、T+1、最小交易单位、账户现金、单票上限、行业/品种限制等。
3. **执行层**：将合规订单提交给券商适配器，并记录订单生命周期。
4. **风控层**：盘前、盘中、盘后检查最大回撤、最大亏损、持仓集中度、异常成交和连接状态。
5. **审计层**：所有信号、约束裁剪、订单、成交和人工干预必须可追溯。

## 快速开始

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
pytest
```

查看设计细节：[`docs/mainland_broker_quant_system.md`](docs/mainland_broker_quant_system.md)。

## A 股 ETF 日频回测

新增的研究闭环负责从 AKShare 获取 ETF 前复权日线、生成只使用当日及历史收盘价的信号，
并在下一交易日开盘执行。引擎内置 A 股 100 份申购单位、佣金、最低佣金和滑点模型，输出
成交记录、每日资金/持仓/权益，以及总收益、年化收益、最大回撤和夏普比率。

安装数据依赖并下载数据：

```python
from kelaode import AKShareETFDownloader

AKShareETFDownloader().download_csv(
    "510300", "20230101", "20231231", "data/510300.csv"
)
```

运行一个完整回测：

```python
from kelaode import ETFBacktester, MovingAverageCrossStrategy, read_daily_bars

bars = read_daily_bars("data/510300.csv")
result = ETFBacktester().run(bars, MovingAverageCrossStrategy(5, 20))
print(result.total_return, result.max_drawdown, result.trades)
```

AKShare 是可选依赖：下载真实行情前运行 `pip install -e '.[data]'`；只运行离线回测和测试
无需安装 AKShare。策略在交易日收盘生成目标仓位，次日开盘成交，从而避免未来函数。

## 多标的 ETF 日频数据层

`DEFAULT_ETF_UNIVERSE` 提供可配置的默认标的池；`MarketDataset` 以稳定的 `DailyBar`
序列保存多个标的，不要求回测代码依赖 pandas。它提供 `history(symbol)`、`on_date(date)`、
`has_bar(symbol, date)`、`all_dates`、`common_dates` 和 `aligned(mode)`。`union` 对齐保留
日期并集并以 `None` 明确表示无行情，`intersection` 仅保留所有标的均有行情的日期；两种
模式都不会 forward-fill。

安装数据依赖后批量下载（每个标的独立重试，失败不会生成合成数据）：

```bash
pip install -e '.[data]'
python -m kelaode.data_cli download \
  --symbols 510300,510500,159915,512100 \
  --start 2015-01-01 --end 2026-07-19 \
  --output data/market/etf_daily --adjust qfq
```

默认写入 Parquet 和 `manifest.json`。需要轻量 CSV 时添加 `--format csv`。校验目录中的
全部数据并输出缺失比例、共同时间范围和价格跳变 warning：

```bash
python -m kelaode.data_cli validate --input data/market/etf_daily
```

manifest 记录数据源、复权方式、请求/实际日期、行数、UTC 下载时间、schema 版本、文件路径
及成功/错误状态；格式由 [`manifest.schema.json`](data/market/etf_daily/manifest.schema.json) 定义。
仓库仅提交 [`manifest.example.json`](data/market/etf_daily/manifest.example.json)，
`.gitignore` 会排除真实历史行情。

## 多资产 ETF 组合日频回测

`PortfolioBacktester` 接受 `symbol -> DailyBar 序列`，策略在交易日 t 收盘后通过只读
`MarketView` 生成完整目标组合（未返回的标的目标权重为 0），并严格在 t+1 开盘先卖后买、
在 t+1 收盘估值。引擎默认只做多、不使用杠杆，支持整手、最低佣金、滑点、现金缓冲、
单标的/总仓位上限和再平衡容差。

```python
from kelaode import (
    EqualWeightBuyAndHold, ETFFeeModel, PortfolioBacktestConfig,
    PortfolioBacktester, read_daily_bars,
)

market = {
    "510300": read_daily_bars("data/510300.csv"),
    "510500": read_daily_bars("data/510500.csv"),
}
engine = PortfolioBacktester(
    PortfolioBacktestConfig(initial_cash=200_000, cash_buffer=0.02),
    ETFFeeModel(commission_rate=0.00025, minimum_commission=5, slippage_rate=0.0005),
)
result = engine.run(market, EqualWeightBuyAndHold(tuple(market)))
print(result.total_return, result.turnover, result.positions_by_date)
```

另有 `PeriodicEqualWeightRebalance`（按月或每 N 个交易日）和
`CrossSectionalMomentumStrategy`（只按截至当日的 N 日收益选择 top-k）作为基线策略。

> 提醒：本项目是工程研究原型，不构成投资建议。任何实盘接入前都必须完成合规确认、券商接口授权、仿真验证和小额灰度。

## Strategy SDK quick start

See [the strategy SDK](docs/strategy_sdk.md) for target-weight, ranking, and signal strategies.
`CrossSectionalMomentumRotation(lookback=60, top_k=3)` provides a point-in-time momentum example.
For importing signal formulas safely, see the [adaptation guide](docs/open_source_strategy_adaptation.md).
# Kelaode

## Strategy experiments

The reproducible experiment layer provides canonical JSON configurations,
Git/data provenance, trading-day fixed and walk-forward splits, deterministic
grid search, robust performance and benchmark statistics, cost scenarios, and
a hash-verified result bundle. The CLI currently executes no-fit runs; retained
grid-search and walk-forward command names fail safely pending validated runners.
See [the experiment guide](docs/experiments.md)
and [`configs/example_momentum.json`](configs/example_momentum.json).
