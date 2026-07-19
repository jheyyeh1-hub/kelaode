"""Direct tests for independent time-series trend decisions."""
from __future__ import annotations
from datetime import date, timedelta
import math
import pytest
from kelaode.market_data import DailyBar
from kelaode.portfolio import HoldTargets, MarketView, PortfolioBacktester, PortfolioBacktestConfig
from kelaode.strategy_registry import create_strategy
from kelaode.time_series_trend import TimeSeriesTrendParameters, TimeSeriesTrendStrategy


def bars(values, start=0, missing=()):
    day=date(2024,1,1); out=[]
    for i,value in enumerate(values):
        if i not in missing:
            out.append(DailyBar(day+timedelta(days=start+i),value,value*1.01,value*.99,value,100000))
    return out

def strategy(symbols=('A','B'), **kw):
    p={'trend_lookback':3,'volatility_lookback':3,'rebalance_frequency':1,'signal_buffer':0.0,'maximum_active_assets':None}
    p.update(kw); return TimeSeriesTrendStrategy(symbols,TimeSeriesTrendParameters(**p))

def target(s,data,idx=None):
    days=sorted({b.trade_date for v in data.values() for b in v}); idx=len(days)-1 if idx is None else idx
    return s.target_weights(idx,days[idx],MarketView(data,days[idx]),None)

def test_positive_and_negative_trends_are_classified_independently():
    got=target(strategy(),{'A':bars([10,11,12,14]),'B':bars([14,13,12,10])})
    assert got['A']>0 and 'B' not in got

def test_buffer_boundary_is_strict():
    data={'A':bars([100,101,102,110]),'B':bars([100,102,104,111])}
    got=target(strategy(signal_buffer=.10),data)
    assert 'A' not in got and got['B']>0

def test_insufficient_trend_and_volatility_history_are_diagnosed():
    s=strategy(trend_lookback=4,volatility_lookback=2)
    assert target(s,{'A':bars([1,2,3]),'B':bars([1,2,4])})=={}
    assert s.diagnostics()['symbols']['A']['eligibility_reason']=='insufficient_trend_history'
    s=strategy(trend_lookback=2,volatility_lookback=4)
    assert target(s,{'A':bars([1,2,3]),'B':bars([1,2,4])})=={}
    assert s.diagnostics()['symbols']['A']['eligibility_reason']=='insufficient_volatility_history'

def test_zero_volatility_is_rejected_safely():
    s=strategy(symbols=('A',)); assert target(s,{'A':bars([10,10,10,10])})=={}
    assert s.diagnostics()['symbols']['A']['eligibility_reason']=='volatility_below_floor'

def test_inverse_volatility_and_normalization():
    data={'A':bars([100,101,102,104]),'B':bars([100,105,101,110])}
    got=target(strategy(),data)
    assert got['A']>got['B'] and math.isclose(sum(got.values()),1,abs_tol=1e-12)

def test_shared_engine_enforces_single_symbol_limit():
    data={'A':bars([100,101,102,104])}
    with pytest.raises(ValueError,match='max_single_weight'):
        PortfolioBacktester(PortfolioBacktestConfig(max_single_weight=.5)).run(data,strategy(('A',)))

def test_capacity_rule_prefers_trend_then_volatility_then_symbol():
    data={'A':bars([100,102,104,110]),'B':bars([100,104,101,112]),'C':bars([100,101,102,110])}
    got=target(strategy(('C','B','A'),maximum_active_assets=2),data)
    assert list(got)==['A','B']  # B strongest; A beats C's equal trend on lower volatility.
    tied={'B':bars([100,101,103,110]),'A':bars([100,101,103,110])}
    assert list(target(strategy(('B','A'),maximum_active_assets=1),tied))==['A']

def test_rebalance_schedule_first_eligible_and_holds_between_updates():
    data={'A':bars([100,101,102,104,105,107]),'B':bars([100,99,100,101,102,103])}; s=strategy(rebalance_frequency=2)
    days=sorted({b.trade_date for v in data.values() for b in v})
    assert isinstance(s.target_weights(2,days[2],MarketView(data,days[2]),None),HoldTargets)
    assert not isinstance(s.target_weights(3,days[3],MarketView(data,days[3]),None),HoldTargets)
    assert isinstance(s.target_weights(4,days[4],MarketView(data,days[4]),None),HoldTargets)
    assert not isinstance(s.target_weights(5,days[5],MarketView(data,days[5]),None),HoldTargets)

def test_order_and_dictionary_order_do_not_change_output():
    one={'A':bars([100,101,102,105]),'B':bars([100,102,103,106])}; two=dict(reversed(list(one.items())))
    assert target(strategy(('B','A')),one)==target(strategy(('A','B')),two)

def test_late_listing_and_missing_current_bar_are_ineligible():
    a=bars([100,101,102,104,106,108]); late=bars([50,51,53],start=3)
    s=strategy(); got=target(s,{'A':a,'B':late})
    assert 'B' not in got
    day=a[-1].trade_date
    assert s.diagnostics()['symbols']['B']['eligibility_reason'] in {'insufficient_trend_history','current_bar_unavailable'}

def test_future_mutation_cannot_change_earlier_signal():
    base={'A':bars([100,101,102,104,105]),'B':bars([100,99,101,103,104])}; day=base['A'][3].trade_date
    before=strategy().target_weights(3,day,MarketView(base,day),None)
    base['A'][-1]=bars([999],start=4)[0]
    after=strategy().target_weights(3,day,MarketView(base,day),None)
    assert before==after

def test_signal_executes_only_at_next_open():
    result=PortfolioBacktester(PortfolioBacktestConfig(lot_size=1,minimum_commission=0)).run(
        {'A':bars([100,101,102,104,106])},strategy(('A',)))
    assert result.orders and all(o.trade_date>o.signal_date for o in result.orders)

def test_parameters_and_registry_are_strict():
    good={'trend_lookback':3,'volatility_lookback':3,'rebalance_frequency':1,'signal_buffer':0,'maximum_active_assets':1}
    assert isinstance(create_strategy('TimeSeriesTrendStrategy',['A'],good),TimeSeriesTrendStrategy)
    for update in ({'trend_lookback':0},{'volatility_lookback':1},{'rebalance_frequency':0},{'signal_buffer':-1},{'maximum_active_assets':0}):
        with pytest.raises(ValueError): TimeSeriesTrendParameters(**{**good,**update})
    with pytest.raises(TypeError): TimeSeriesTrendParameters(**good,unknown=1)
    with pytest.raises(ValueError): create_strategy('TimeSeriesTrendStrategy',['A'],{**good,'maximum_active_assets':2})
