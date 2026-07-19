"""Black-box schema-2 coverage for the independent TSMOM family."""
from __future__ import annotations
import csv, hashlib, json, shutil, socket
from dataclasses import replace
from pathlib import Path
from kelaode.experiment import ExperimentConfig
from kelaode.runner import run_experiment
from kelaode.selection_runner import run_fixed_selection, run_walk_forward, validate_artifact_directory
from kelaode.validation_audit import audit_time_series_trend_diagnostics

REPO=Path(__file__).parents[1]
FIXTURE=REPO/'tests/fixtures/tsmom_snapshot'

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

def test_standard_bundle_cache_offline_diagnostics_execution_and_benchmark(tmp_path,monkeypatch):
    def blocked(*args,**kwargs): raise AssertionError('network access attempted')
    monkeypatch.setattr(socket,'create_connection',blocked)
    cfg=config('tsmom_synthetic_run.json',tmp_path)
    root=run_experiment(cfg); before=hashes(root)
    assert run_experiment(cfg)==root and hashes(root)==before
    manifest=json.loads((root/'artifact_manifest.json').read_text())
    assert set(manifest['artifacts'])=={p.name for p in root.iterdir()
                                       if p.is_file() and p.name!='artifact_manifest.json'}
    assert set(audit_time_series_trend_diagnostics(root))=={
        'point_in_time_diagnostics_present','inverse_volatility_targets_reconstructed',
        'active_count_reconstructed','target_concentration_reconstructed'}
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

def test_fixed_selection_twice_is_identical_and_test_is_isolated(tmp_path):
    cfg=config('tsmom_synthetic_fixed.json',tmp_path)
    root=run_fixed_selection(cfg); before=hashes(root)
    assert run_fixed_selection(cfg)==root and hashes(root)==before
    validate_artifact_directory(root)
    result=json.loads((root/'result.json').read_text())
    assert len(list((root/'frozen_test').glob('*/artifact_manifest.json')))==1
    assert all(not list((root/'candidates'/row['candidate_id']).glob('**/frozen_test'))
               for row in result['candidate_table'])
    child=root/result['frozen_test_bundle']
    audit_time_series_trend_diagnostics(child)

def test_walk_forward_twice_has_disjoint_continuous_oos(tmp_path):
    cfg=config('tsmom_synthetic_walk_forward.json',tmp_path)
    root=run_walk_forward(cfg); before=hashes(root)
    assert run_walk_forward(cfg)==root and hashes(root)==before
    validate_artifact_directory(root)
    result=json.loads((root/'result.json').read_text()); seen=set(); ordered=[]
    for fold in result['folds']:
        train=set(fold['boundaries']['train']); valid=set(fold['boundaries']['validation']); test=set(fold['boundaries']['test'])
        assert train.isdisjoint(valid|test) and valid.isdisjoint(test) and seen.isdisjoint(test)
        seen|=test; ordered.extend(fold['boundaries']['test'])
        audit_time_series_trend_diagnostics(root/f"fold-{fold['fold']:04d}"/fold['frozen_test_bundle'])
    stitched=[r['date'] for r in rows(root/'stitched_oos_equity.csv')]
    assert stitched==ordered==sorted(ordered)
