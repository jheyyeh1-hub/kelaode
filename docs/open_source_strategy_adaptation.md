# Adapting open-source strategies

Migrate formulas, indicator parameters, signal rules, and documented rebalance schedules—not
broker clients, framework event loops, global data caches, order calls, or framework portfolio
objects. Review the source license and attribution requirements; do not copy whole repositories.

`CallableSignalAdapter` wraps a plain function. `DataFrameSignalAdapter` constructs a fresh table
from bars no later than `MarketView.current_date`, calls external logic, and accepts `signal`,
`score`, or `target_weight`. Backtrader, Freqtrade, and JoinQuant template classes document how to
extract respectively indicator/`next`, populate methods, and lifecycle callbacks without installing
those frameworks. Translate their order effects into signals or target weights and let kelaode
perform execution. Never hand an adapter the full future history.
