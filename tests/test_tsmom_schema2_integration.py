"""Black-box schema-2 coverage for the independent TSMOM family."""
from __future__ import annotations
import csv, hashlib, json, shutil, socket
from dataclasses import replace
from pathlib import Path
import pytest
from kelaode.experiment import ExperimentConfig
from kelaode.experiment import experiment_identity, experiment_metadata
from kelaode.runner import run_experiment
from kelaode.selection_runner import run_fixed_selection, run_walk_forward, validate_artifact_directory
from kelaode.snapshot import SnapshotManifest, sha256_file
from kelaode.validation_audit import audit_selection, audit_time_series_trend_diagnostics

REPO=Path(__file__).parents[1]
FIXTURE=REPO/'tests/fixtures/tsmom_snapshot'
POLICY=REPO/'configs/audit/tsmom_synthetic_policy.json'

def config(name,tmp_path):
    raw=json.loads((REPO/'configs'/name).read_text()); raw.update(
        data_root=str(FIXTURE),data_manifest=str(FIXTURE/'manifest.json'),
        output_directory=str(tmp_path/name))
    return ExperimentConfig.from_json(json.dumps(raw))

def rows(path):
    with path.open(newline='',encoding='utf8') as f:return list(csv.DictReader(f))

def hashes(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}

def reseal(root):
    for path in sorted(root.glob('**/artifact_manifest.json'),key=lambda p:len(p.parts),reverse=True):
        value=json.loads(path.read_text()); directory=path.parent
        value['artifacts']={name:sha256_file(directory/name) for name in value.get('artifacts',{})}
        if 'children' in value:
            value['children']={relative:sha256_file(directory/relative/'artifact_manifest.json')
                               for relative in value['children']}
        path.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n')

def selected_children(root):
    result=json.loads((root/'result.json').read_text())
    if result['mode']=='fixed_selection': return [root/result['frozen_test_bundle']]
    return [root/f"fold-{fold['fold']:04d}"/fold['frozen_test_bundle'] for fold in result['folds']]

def assert_warmup_boundary(child):
    cfg=json.loads((child/'configuration.json').read_text()); boundary=cfg['execution_start_date']
    for name in ('orders.csv','fills.csv','benchmark_orders.csv','benchmark_fills.csv'):
        assert all(row['date']>=boundary for row in rows(child/name))
    for name in ('positions.csv','benchmark_positions.csv'):
        assert not [row for row in rows(child/name)
                    if row['date']<boundary and int(row['quantity'])!=0]
    orders={(row['date'],row['symbol']):row['signal_date'] for row in rows(child/'orders.csv')}
    assert all(row['date']>orders[(row['date'],row['symbol'])] for row in rows(child/'fills.csv'))
    audits=json.loads((child/'daily_audits.json').read_text())
    first_eligible_rebalance=next(row['date'] for row in audits
                                  if row['strategy_diagnostics']['rebalance'])
    assert first_eligible_rebalance<boundary
    assert all(row['date']>=boundary for row in rows(child/'orders.csv'))

def assert_cost_monotonicity(costs):
    closed=costs['closed_loop']; fixed=costs['fixed_path']
    assert {v['label'] for v in closed.values()}=={'closed-loop execution rerun'}
    assert {v['label'] for v in fixed.values()}=={'fixed frozen fill-path repricing'}
    assert closed['moderate']['metrics']['total_return']<=closed['base']['metrics']['total_return']
    assert closed['severe']['metrics']['total_return']<=closed['moderate']['metrics']['total_return']
    assert fixed['moderate']['final_equity']<=fixed['base']['final_equity']
    assert fixed['severe']['final_equity']<=fixed['moderate']['final_equity']

def test_standard_bundle_cache_offline_diagnostics_execution_and_benchmark(tmp_path,monkeypatch):
    def blocked(*args,**kwargs): raise AssertionError('network access attempted')
    monkeypatch.setattr(socket,'create_connection',blocked)
    cfg=config('tsmom_synthetic_run.json',tmp_path)
    root=run_experiment(cfg); before=hashes(root)
    assert run_experiment(cfg)==root and hashes(root)==before
    manifest=json.loads((root/'artifact_manifest.json').read_text())
    assert set(manifest['artifacts'])=={p.name for p in root.iterdir()
                                       if p.is_file() and p.name!='artifact_manifest.json'}
    checks=audit_time_series_trend_diagnostics(root)
    assert {'market_data_trend_reconstructed','market_data_sample_volatility_reconstructed',
            'capacity_selection_reconstructed','target_weight_total_valid'} <= set(checks)
    orders=rows(root/'orders.csv')
    assert all(r['date']>r['signal_date'] for r in orders)
    benchmark_positions=rows(root/'benchmark_positions.csv')
    benchmark_fills=rows(root/'benchmark_fills.csv')
    curve=[float(r['equity']) for r in rows(root/'benchmark_curve.csv')]
    assert benchmark_fills and any(int(r['quantity']) for r in benchmark_positions)
    assert len(set(curve))>1
    audits=json.loads((root/'daily_audits.json').read_text())
    first_rebalance=next(r['date'] for r in audits if r['strategy_diagnostics']['rebalance'])
    assert not [r for r in orders if r['date']<=first_rebalance]

def test_identity_mutations_and_no_sit_artifact_dependency(tmp_path):
    cfg=config('tsmom_synthetic_run.json',tmp_path); root=run_experiment(cfg)
    changed=dict(cfg.strategy_parameters); changed['signal_buffer']=.01
    assert run_experiment(replace(cfg,strategy_parameters=changed)).name!=root.name
    copied=tmp_path/'copy'; shutil.copytree(FIXTURE,copied)
    path=copied/'DOWN.csv'; path.write_bytes(path.read_bytes()+b'\n')
    manifest=json.loads((copied/'manifest.json').read_text())
    entry=next(x for x in manifest['entries'] if x['symbol']=='DOWN')
    entry['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    (copied/'manifest.json').write_text(json.dumps(manifest))
    mutated=replace(cfg,data_root=str(copied),data_manifest=str(copied/'manifest.json'),
                    output_directory=str(tmp_path/'mutated'))
    assert run_experiment(mutated).name!=root.name
    identity=json.loads((root/'identity.json').read_text())
    assert 'sit_snapshot' not in json.dumps(identity).lower()

def test_result_affecting_source_fingerprint_changes_identity_without_checkout_mutation(tmp_path):
    cfg=config('tsmom_synthetic_run.json',tmp_path); manifest=SnapshotManifest.load(cfg.data_manifest)
    metadata=experiment_metadata(cfg,cfg.data_manifest)
    first=experiment_identity(cfg,manifest,metadata)
    assert first==experiment_identity(cfg,manifest,metadata)
    changed={**metadata,'source_tree_sha256':'0'*64}
    assert experiment_identity(cfg,manifest,changed)['experiment_id']!=first['experiment_id']

def test_fixed_selection_twice_is_identical_and_test_is_isolated(tmp_path):
    cfg=config('tsmom_synthetic_fixed.json',tmp_path)
    root=run_fixed_selection(cfg); before=hashes(root)
    assert run_fixed_selection(cfg)==root and hashes(root)==before
    validate_artifact_directory(root)
    formal=audit_selection(root,POLICY)
    assert formal['status']=='pass' and formal['bundle_count']==1
    result=json.loads((root/'result.json').read_text())
    assert len(list((root/'frozen_test').glob('*/artifact_manifest.json')))==1
    assert all(not list((root/'candidates'/row['candidate_id']).glob('**/frozen_test'))
               for row in result['candidate_table'])
    child=root/result['frozen_test_bundle']
    audit_time_series_trend_diagnostics(child)
    assert_warmup_boundary(child)
    costs=result['cost_analysis']
    assert_cost_monotonicity(costs)

def test_walk_forward_twice_has_disjoint_continuous_oos(tmp_path):
    cfg=config('tsmom_synthetic_walk_forward.json',tmp_path)
    root=run_walk_forward(cfg); before=hashes(root)
    assert run_walk_forward(cfg)==root and hashes(root)==before
    validate_artifact_directory(root)
    formal=audit_selection(root,POLICY)
    assert formal['status']=='pass'
    result=json.loads((root/'result.json').read_text()); seen=set(); ordered=[]
    for fold in result['folds']:
        train=set(fold['boundaries']['train']); valid=set(fold['boundaries']['validation']); test=set(fold['boundaries']['test'])
        assert train.isdisjoint(valid|test) and valid.isdisjoint(test) and seen.isdisjoint(test)
        seen|=test; ordered.extend(fold['boundaries']['test'])
        audit_time_series_trend_diagnostics(root/f"fold-{fold['fold']:04d}"/fold['frozen_test_bundle'])
        assert_warmup_boundary(root/f"fold-{fold['fold']:04d}"/fold['frozen_test_bundle'])
        assert_cost_monotonicity(fold['cost_analysis'])
    stitched=[r['date'] for r in rows(root/'stitched_oos_equity.csv')]
    assert stitched==ordered==sorted(ordered)

def test_cost_artifact_and_cash_only_benchmark_mutations_fail_formal_audit(tmp_path):
    root=run_fixed_selection(config('tsmom_synthetic_fixed.json',tmp_path/'base'))
    cost_copy=tmp_path/'cost'; shutil.copytree(root,cost_copy)
    result=json.loads((cost_copy/'result.json').read_text())
    result['cost_analysis']['fixed_path_contract']['base_fill_path_sha256']='0'*64
    (cost_copy/'result.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n'); reseal(cost_copy)
    with pytest.raises(ValueError,match='fill SHA'): audit_selection(cost_copy,POLICY)
    cash_copy=tmp_path/'cash'; shutil.copytree(root,cash_copy)
    child=selected_children(cash_copy)[0]
    curve=rows(child/'benchmark_curve.csv')
    with (child/'benchmark_curve.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=('date','equity'));writer.writeheader()
        writer.writerows({'date':row['date'],'equity':100000} for row in curve)
    reseal(cash_copy)
    with pytest.raises(ValueError): audit_selection(cash_copy,POLICY)

@pytest.fixture
def diagnostic_bundle(tmp_path):
    return run_experiment(config('tsmom_synthetic_run.json',tmp_path))

def _rebalance_record(value):
    return next(row for row in value if row['strategy_diagnostics']['rebalance'] and
                row['strategy_diagnostics']['active_asset_count'])

@pytest.mark.parametrize('mutation',[
    'trend_signal','realized_volatility','raw_inverse_volatility','eligibility_reason',
    'maximum_active_assets','active_asset_count','target_concentration','normalized_target_weight'])
def test_resealed_diagnostic_mutations_are_rejected(diagnostic_bundle,tmp_path,mutation):
    root=tmp_path/mutation; shutil.copytree(diagnostic_bundle,root)
    path=root/'daily_audits.json'; value=json.loads(path.read_text())
    record=(next(row for row in value if any(
        item['eligibility_reason']=='maximum_active_assets'
        for item in row['strategy_diagnostics']['symbols'].values()))
        if mutation=='maximum_active_assets' else _rebalance_record(value))
    diagnostic=record['strategy_diagnostics']; symbols=diagnostic['symbols']
    selected=next(symbol for symbol,item in symbols.items() if item['eligibility_reason']=='eligible')
    if mutation=='trend_signal': symbols[selected]['trend_signal']+=.01
    elif mutation=='realized_volatility': symbols[selected]['realized_volatility']*=2
    elif mutation=='raw_inverse_volatility': symbols[selected]['raw_inverse_volatility']*=2
    elif mutation=='eligibility_reason': symbols[selected]['eligibility_reason']='trend_not_above_buffer'
    elif mutation=='maximum_active_assets':
        excluded=next(symbol for symbol,item in symbols.items()
                      if item['eligibility_reason']=='maximum_active_assets')
        symbols[excluded]['eligibility_reason']='eligible'
        symbols[excluded]['raw_inverse_volatility']=1/symbols[excluded]['realized_volatility']
    elif mutation=='active_asset_count': diagnostic['active_asset_count']+=1
    elif mutation=='target_concentration': diagnostic['target_concentration']+=.01
    else: symbols[selected]['normalized_target_weight']+=.01
    path.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n'); reseal(root)
    with pytest.raises(ValueError): audit_time_series_trend_diagnostics(root)

def test_future_snapshot_change_does_not_change_reconstructed_earlier_signal(tmp_path):
    baseline=run_experiment(config('tsmom_synthetic_run.json',tmp_path/'baseline'))
    copied=tmp_path/'snapshot'; shutil.copytree(FIXTURE,copied)
    data=copied/'UP_LOW.csv'; lines=data.read_text().splitlines()
    fields=lines[-1].split(',')
    fields[1:5]=['120.000000','121.000000','119.000000','120.500000']
    lines[-1]=','.join(fields)
    data.write_text('\n'.join(lines)+'\n')
    manifest=json.loads((copied/'manifest.json').read_text())
    next(entry for entry in manifest['entries'] if entry['symbol']=='UP_LOW')['sha256']=hashlib.sha256(data.read_bytes()).hexdigest()
    (copied/'manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n')
    cfg=config('tsmom_synthetic_run.json',tmp_path/'changed')
    cfg=replace(cfg,data_root=str(copied),data_manifest=str(copied/'manifest.json'))
    changed=run_experiment(cfg)
    audit_time_series_trend_diagnostics(baseline); audit_time_series_trend_diagnostics(changed)
    first=json.loads((baseline/'daily_audits.json').read_text())
    second=json.loads((changed/'daily_audits.json').read_text())
    cutoff=lines[-1].split(',')[0]
    project=lambda rows:[row['strategy_diagnostics'] for row in rows if row['date']<cutoff]
    assert project(first)==project(second)
