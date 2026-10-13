"""Public behavioral regressions; all inputs generated and effects intercepted."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
from private_storage_helpers import make_repository
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
import em_tick as tick
import em_draft_lint
FIX = json.loads((Path(__file__).parent / 'reliability.json').read_text(encoding='utf-8'))

def ack(key, adapter='alert', status='confirmed'):
    return {'status': status, 'idempotency_key': key, 'adapter': adapter,
            'receipt_id': 'receipt-11', 'evidence': 'synthetic disposition proof'}

@pytest.fixture
def harness(tmp_path, monkeypatch):
    companion = tmp_path / 'companion'
    make_repository(companion)
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    monkeypatch.setattr(tick, 'LOG', str(companion / 'log.txt'))
    stored = {'value': {'cursors': {}, 'seen_gm_msgids': []}}
    saves, effects = [], []
    fetched = {'records': [copy.deepcopy(FIX['message'])]}
    monkeypatch.setattr(tick, 'resolve_app_pw', lambda *a: 'synthetic-password')
    monkeypatch.setattr(tick.em_watch, 'load_state', lambda p: stored['value'])
    def save(path, value):
        saves.append(copy.deepcopy(value))
        stored['value'] = copy.deepcopy(value)
    monkeypatch.setattr(tick.em_watch, 'save_state', save)
    monkeypatch.setattr(tick.em_watch, 'run_once', lambda *a, **k:
                        (copy.deepcopy(fetched['records']), copy.deepcopy(FIX['cursor'])))
    monkeypatch.setattr(tick, 'classify_records_parallel', lambda msgs, *a:
                        [copy.deepcopy(FIX['verdict']) for _ in msgs])
    def send(message, idempotency_key=None, **kw):
        effects.append(idempotency_key)
        assert stored['value']['actions'][idempotency_key]['status'] == 'uncertain'
        return ack(idempotency_key)
    monkeypatch.setattr(tick.em_alert, 'send', send)
    def run(dry=False, **kw):
        return tick.process_account(copy.deepcopy(FIX['account']), {}, 'absent', None,
                                    None, str(companion / 'state'), dry,
                                    agent_cfg={'mode': 'heuristic'}, pool_enabled=False, **kw)
    return SimpleNamespace(run=run, stored=stored, saves=saves, effects=effects,
                           fetched=fetched, companion=companion)


@pytest.mark.parametrize('receipt', [None])
def test_ambiguous_ack_is_uncertain_and_never_blindly_replayed(harness, monkeypatch, receipt):
    monkeypatch.setattr(tick.em_alert, 'send', lambda *a, **k: harness.effects.append(k) or receipt)
    assert harness.run()['status'] == 'incomplete'
    harness.fetched['records'] = []
    assert harness.run()['status'] == 'incomplete'
    assert len(harness.effects) == 1
    assert next(iter(harness.stored['value']['actions'].values()))['status'] == 'uncertain'


def test_failed_intent_save_prevents_effect(harness, monkeypatch):
    def fail(*a):
        raise OSError('synthetic disk failure')
    monkeypatch.setattr(tick.em_watch, 'save_state', fail)
    assert harness.run()['status'] == 'failed'
    assert not harness.effects

def test_unversioned_storage_fails_before_credentials(harness, monkeypatch, capsys):
    import shutil
    _configured_registry(harness, monkeypatch)
    shutil.rmtree(harness.companion / '.git')
    monkeypatch.setattr(tick, 'resolve_app_pw', lambda *a: pytest.fail('credentials reached'))
    monkeypatch.setattr(sys, 'argv', ['em_tick.py', '--config', str(harness.companion/'registry.json')])
    assert tick.main() != 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result['status'] == 'failed' and 'Git companion' in result['error']
    assert not harness.effects

def test_local_only_agent_fails_before_credentials(harness, monkeypatch):
    monkeypatch.setattr(tick, 'resolve_app_pw', lambda *a: pytest.fail('credentials reached'))
    result = tick.process_account(FIX['account'], {}, 'absent', None, None,
                                  str(harness.companion / 'state'), True,
                                  runtime={'local_only': True})
    assert result['status'] == 'failed'


@pytest.mark.parametrize('draft', [{'language': 'invalid'}])
def test_bad_draft_configuration_fails_closed(draft):
    with pytest.raises(ValueError):
        em_draft_lint.lint(FIX['draft_text'], 'business', config=draft)


def test_crash_after_effect_retains_uncertainty_across_restart(harness, monkeypatch):
    def interrupted(*a, **kw):
        harness.effects.append(kw['idempotency_key'])
        raise KeyboardInterrupt('synthetic interruption after delivery')
    monkeypatch.setattr(tick.em_alert, 'send', interrupted)
    with pytest.raises(KeyboardInterrupt):
        harness.run()
    assert next(iter(harness.stored['value']['actions'].values()))['status'] == 'uncertain'
    harness.fetched['records'] = []
    assert harness.run()['status'] == 'incomplete'
    assert len(harness.effects) == 1


def _configured_registry(harness, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]/'scripts'))
    import init_config
    root = harness.companion
    argv = ['init_config.py', '--out', str(root)]
    monkeypatch.setattr(sys, 'argv', argv)
    assert init_config.main() == 0
    cfg = json.loads((root/'registry.json').read_text())
    cfg['accounts'] = [{**FIX['account'], 'role': 'primary'}]
    cfg['runtime'] = {'python': sys.executable, 'local_only': True}
    cfg['classifier'] = {'mode': 'heuristic'}
    cfg['draft'] = FIX['draft']
    cfg['storage'] = {'state_dir': 'data/state', 'db': 'data/pool.db', 'log': 'data/log.txt'}
    (root/'registry.json').write_text(json.dumps(cfg))
    (root/'rules'/'sender_map.json').write_text(json.dumps(
        {'version': 1, 'by_address': {FIX['message']['from']: FIX['model_policy']['topic_config']['allowed_labels'][0]},
         'by_domain': {}, 'by_list_id': {}}))
    (root/'rules'/'labels.json').write_text(json.dumps({'user1': ['Review']}))
    return cfg
