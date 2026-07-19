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

> 提醒：本项目是工程研究原型，不构成投资建议。任何实盘接入前都必须完成合规确认、券商接口授权、仿真验证和小额灰度。
