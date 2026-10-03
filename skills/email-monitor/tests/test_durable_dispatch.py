"""Public behavioral regressions; all inputs generated and effects intercepted."""
import copy
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
from private_storage_helpers import make_repository, make_linked_worktree, write_visibility
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
    write_visibility(tmp_path)
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

def test_dry_preserves_loaded_state_and_has_no_writes(harness, monkeypatch, capsys):
    before = copy.deepcopy(harness.stored['value'])
    monkeypatch.setenv('GMAIL_APP_PW', 'parent-value')
    result = harness.run(dry=True)
    assert result['status'] == 'planned' and result['planned_actions']
    assert not harness.saves and not harness.effects
    assert harness.stored['value'] == before
    assert os.environ['GMAIL_APP_PW'] == 'parent-value'
    assert not (harness.companion / 'log.txt').exists()
    assert 'planned' in capsys.readouterr().out

def test_confirmation_requires_durable_uncertainty_and_replay_is_noop(harness):
    assert harness.run()['status'] == 'completed'
    row = next(iter(harness.stored['value']['actions'].values()))
    assert row['receipt'] == ack(row['idempotency_key'])
    assert row['account'] == FIX['account']['user'] and row['mailbox'] == 'INBOX'
    assert row['uidvalidity'] == FIX['cursor']['uidvalidity']
    assert harness.run()['status'] == 'completed'
    assert len(harness.effects) == 1

@pytest.mark.parametrize('receipt', [True, False, None, {}, {'status': 'confirmed'}])
def test_ambiguous_ack_is_uncertain_and_never_blindly_replayed(harness, monkeypatch, receipt):
    monkeypatch.setattr(tick.em_alert, 'send', lambda *a, **k: harness.effects.append(k) or receipt)
    assert harness.run()['status'] == 'incomplete'
    harness.fetched['records'] = []
    assert harness.run()['status'] == 'incomplete'
    assert len(harness.effects) == 1
    assert next(iter(harness.stored['value']['actions'].values()))['status'] == 'uncertain'

def test_reconciliation_checkpoints_confirmation_without_resend(harness, monkeypatch):
    monkeypatch.setattr(tick.em_alert, 'send', lambda *a, **k: harness.effects.append(k))
    assert harness.run()['status'] == 'incomplete'
    monkeypatch.setattr(tick, 'reconcile_action', lambda row: ack(row['idempotency_key']))
    harness.fetched['records'] = []
    assert harness.run()['status'] == 'completed'
    assert len(harness.effects) == 1

def test_failed_intent_save_prevents_effect(harness, monkeypatch):
    def fail(*a):
        raise OSError('synthetic disk failure')
    monkeypatch.setattr(tick.em_watch, 'save_state', fail)
    assert harness.run()['status'] == 'failed'
    assert not harness.effects

def test_unversioned_storage_fails_before_credentials(harness, monkeypatch):
    (harness.companion / '.git' / 'config').unlink()
    monkeypatch.setattr(tick, 'resolve_app_pw', lambda *a: pytest.fail('credentials reached'))
    assert harness.run()['status'] == 'failed'
    assert not harness.effects

def test_local_only_agent_fails_before_credentials(harness, monkeypatch):
    monkeypatch.setattr(tick, 'resolve_app_pw', lambda *a: pytest.fail('credentials reached'))
    result = tick.process_account(FIX['account'], {}, 'absent', None, None,
                                  str(harness.companion / 'state'), True,
                                  runtime={'local_only': True})
    assert result['status'] == 'failed'

def test_configured_unicode_signature_and_style():
    assert em_draft_lint.lint(FIX['draft_text'], 'business', config=FIX['draft']) == []
    assert em_draft_lint.lint(FIX['draft_text'] + '\nextra', 'business', config=FIX['draft'])

@pytest.mark.parametrize('fn', [tick.archive, tick._label_add])
def test_label_adapter_requires_structured_confirmation(monkeypatch, fn):
    monkeypatch.setattr(tick.subprocess, 'run', lambda *a, **kw:
                        SimpleNamespace(returncode=0, stdout='', stderr=''))
    assert not fn(FIX['account']['user'], FIX['message']['message_id'], 'Review', False)

def test_main_dry_config_failure_never_logs_or_alerts(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(sys, 'argv', ['em_tick.py', '--config', str(tmp_path/'absent'), '--dry'])
    monkeypatch.setattr(tick, 'log', lambda *a: pytest.fail('persistent logger reached'))
    monkeypatch.setattr(tick.em_alert, 'send', lambda *a: pytest.fail('alert reached'))
    assert tick.main() != 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])['status'] == 'failed'

@pytest.mark.parametrize('linked', [False, True])
def test_private_normal_and_linked_storage_proof(harness, tmp_path, linked):
    import em_runtime
    root = harness.companion
    if linked:
        make_linked_worktree(root, tmp_path / 'common')
    proof = em_runtime.prove_private(root/'data'/'state')
    assert proof['visibility'] == 'PRIVATE'

@pytest.mark.parametrize('draft', [{'language': 'invalid'}, {'style': {'max_lines': 0}},
                                  {'style': {'allow_markdown': 'false'}}, {'signature': ''}])
def test_bad_draft_configuration_fails_closed(draft):
    with pytest.raises(ValueError):
        em_draft_lint.lint(FIX['draft_text'], 'business', config=draft)

def test_doctor_json_reports_bounded_runtime_failure(harness, monkeypatch, capsys):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]/'scripts'))
    import verify_config
    cfg = {'schema_version': 1, 'mode': 'B', 'accounts': [FIX['account']],
           'runtime': {'python': str(harness.companion/'missing-python')}}
    (harness.companion/'registry.json').write_text(json.dumps(cfg))
    monkeypatch.setattr(sys, 'argv', ['verify_config.py', '--config-dir', str(harness.companion), '--json'])
    assert verify_config.main() != 0
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'not_ready' and report['checks']

@pytest.mark.parametrize('adapter', ['archive', 'topic_label'])
def test_structured_label_receipt_is_confirmed_only_when_matched(harness, monkeypatch, adapter):
    receipt = {**ack('action-key', adapter), 'matched': 1, 'applied': True}
    monkeypatch.setattr(tick.subprocess, 'run', lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout=json.dumps(receipt), stderr=''))
    fn = tick.archive if adapter == 'archive' else tick._label_add
    assert fn(FIX['account']['user'], FIX['message']['message_id'], 'Review', False,
              idempotency_key='action-key') == receipt
    receipt['matched'] = 0
    assert fn(FIX['account']['user'], FIX['message']['message_id'], 'Review', False,
              idempotency_key='action-key')['status'] != 'confirmed'


def test_reconcile_proven_not_applied_retries_same_key(harness, monkeypatch):
    def first(*a, **kw):
        harness.effects.append(kw['idempotency_key'])
        return None
    monkeypatch.setattr(tick.em_alert, 'send', first)
    assert harness.run()['status'] == 'incomplete'
    monkeypatch.setattr(tick, 'reconcile_action', lambda row:
                        ack(row['idempotency_key'], status='not_applied'))
    def recovered(*a, **kw):
        harness.effects.append(kw['idempotency_key'])
        return ack(kw['idempotency_key'])
    monkeypatch.setattr(tick.em_alert, 'send', recovered)
    harness.fetched['records'] = []
    assert harness.run()['status'] == 'completed'
    assert len(harness.effects) == 2 and harness.effects[0] == harness.effects[1]


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


def test_checkpoint_failure_after_confirmation_keeps_disk_uncertain(harness, monkeypatch):
    save = tick.em_watch.save_state
    def fail_completed(path, state):
        if any(row['status'] == 'completed' for row in state['actions'].values()):
            raise OSError('synthetic failed completion checkpoint')
        save(path, state)
    monkeypatch.setattr(tick.em_watch, 'save_state', fail_completed)
    assert harness.run()['status'] == 'incomplete'
    assert next(iter(harness.stored['value']['actions'].values()))['status'] == 'uncertain'
    assert harness.run()['status'] == 'incomplete'
    assert len(harness.effects) == 1


@pytest.mark.parametrize('queue', [None, {}, [None], [{'attempts': 1}]])
def test_malformed_legacy_queue_is_preserved_and_reported(harness, queue):
    harness.stored['value']['topic_retry'] = queue
    before = copy.deepcopy(harness.stored['value'])
    assert harness.run()['status'] == 'failed'
    assert harness.stored['value'] == before and not harness.effects and not harness.saves


def test_topic_dry_plans_without_effect_or_state_mutation(harness, monkeypatch):
    state = {'topic_retry': []}
    before = copy.deepcopy(state)
    monkeypatch.setattr(tick.em_topic, 'load_config', lambda *a, **k:
                        {'taxonomy': 'Review', 'sender_map': {}, 'allowed_labels': ['Review'], 'type_labels': []})
    monkeypatch.setattr(tick.em_topic, 'judge', lambda *a, **k:
                        {'state': 'decided', 'labels': [{'label': 'Review'}]})
    monkeypatch.setattr(tick, '_label_add', lambda *a, **k: pytest.fail('dry label effect'))
    assert tick.topic_label(FIX['account']['user'], 'user1', [FIX['message']], True, state=state) == 1
    assert state == before and not (harness.companion/'log.txt').exists()

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


def test_doctor_private_config_accepts_absolute_runtime_resource(harness, monkeypatch, capsys):
    import em_runtime
    _configured_registry(harness, monkeypatch)
    import verify_config
    import importlib.util
    fixture_path = Path(__file__).resolve().parents[3] / 'tools' / 'make_fixtures.py'
    spec = importlib.util.spec_from_file_location('doctor_git_fixture', fixture_path)
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    metadata = harness.companion / '.git'
    for directory in ('objects', 'refs/heads'):
        (metadata / directory).mkdir(parents=True, exist_ok=True)
    (metadata / 'HEAD').write_text(generator.doctor_cases()['git_head'], encoding='utf-8')
    calls = []
    real_run = em_runtime.subprocess.run

    def interpreter(args, **kw):
        if not args or args[0] != sys.executable:
            return real_run(args, **kw)
        calls.append((args, kw))
        return SimpleNamespace(returncode=0, stdout=json.dumps({'email_monitor_runtime': 1, 'llmcall': True}))
    monkeypatch.setattr(em_runtime.subprocess, 'run', interpreter)
    monkeypatch.setattr(sys, 'argv', ['verify_config.py', '--config-dir', str(harness.companion), '--json'])
    capsys.readouterr()
    assert verify_config.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'ready'
    assert calls[0][0][0] == sys.executable and 0 < calls[0][1]['timeout'] <= 30


def test_main_dry_uses_companion_relative_paths_without_disk_effects(harness, monkeypatch, capsys):
    import em_runtime
    _configured_registry(harness, monkeypatch)
    monkeypatch.setattr(em_runtime, 'probe_interpreter', lambda p: (True, 'verified by fake'))
    monkeypatch.setattr(tick, 'preflight', lambda *a: [])
    monkeypatch.setattr(tick.em_pool, 'available', lambda *a: False)
    before = {str(p.relative_to(harness.companion)): p.read_bytes()
              for p in harness.companion.rglob('*') if p.is_file()}
    monkeypatch.setattr(sys, 'argv', ['em_tick.py', '--config', str(harness.companion/'registry.json'), '--dry'])
    assert tick.main() == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result['status'] == 'planned' and result['results'][0]['planned_actions']
    assert result['storage_checks']['state_dir']['path'] == str(harness.companion/'data'/'state')
    after = {str(p.relative_to(harness.companion)): p.read_bytes()
             for p in harness.companion.rglob('*') if p.is_file()}
    assert before == after and not harness.effects and not harness.saves


def test_actions_distinguish_mailboxes_and_rotated_generations(harness):
    first = harness.run()
    assert first['status'] == 'completed'
    # A second scope is legitimate new work even with the same RFC Message-ID.
    original = tick.em_watch.run_once
    tick.em_watch.run_once = lambda *a, **k: (copy.deepcopy(harness.fetched['records']),
                                             {**FIX['cursor'], 'uidvalidity': 8})
    try:
        assert harness.run()['status'] == 'completed'
        assert len(harness.effects) == 2
    finally:
        tick.em_watch.run_once = original
    account = {**FIX['account'], 'monitored_folders': ['INBOX', 'Review']}
    assert tick.process_account(account, {}, 'absent', None, None, str(harness.companion/'state'),
                                False, agent_cfg={'mode': 'heuristic'}, pool_enabled=False)['status'] == 'completed'
    assert len(harness.effects) == 3 and len(set(harness.effects)) == 3

def test_summary_uncertain_alert_never_marks_done_or_resends(harness, monkeypatch, capsys):
    import em_summary
    _configured_registry(harness, monkeypatch)
    calls = []
    monkeypatch.setattr(em_summary, 'assemble', lambda *a, **k: FIX['draft_text'])
    monkeypatch.setattr(em_summary.em_pool, 'due', lambda *a, **k: {'items': [
        {'id': 'summary-event', 'ext': {'x_email_monitor_kind': 'daily-summary'}}]})
    monkeypatch.setattr(em_summary.em_alert, 'send', lambda *a, **k: calls.append('alert'))
    monkeypatch.setattr(em_summary.em_pool, 'mark_done', lambda *a, **k: pytest.fail('unconfirmed summary marked done'))
    monkeypatch.setattr(sys, 'argv', ['em_summary.py', '--config', str(harness.companion/'registry.json')])
    assert em_summary.main() != 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])['status'] == 'incomplete'
    assert em_summary.main() != 0
    assert calls == ['alert']

def test_main_summary_timeout_is_not_success(harness, monkeypatch, capsys):
    import em_runtime
    _configured_registry(harness, monkeypatch)
    monkeypatch.setattr(em_runtime, 'probe_interpreter', lambda p: (True, 'synthetic probe'))
    monkeypatch.setattr(tick, 'preflight', lambda *a: [])
    monkeypatch.setattr(tick.em_pool, 'available', lambda *a: True)
    monkeypatch.setattr(tick.em_pool, 'upsert', lambda *a, **k: ack(k['idempotency_key'], 'pool'))
    real_run = tick.subprocess.run
    def timeout(args, **kwargs):
        if args and Path(args[0]).stem.lower() == 'git':
            return real_run(args, **kwargs)
        raise tick.subprocess.TimeoutExpired('summary worker', 1)
    monkeypatch.setattr(tick.subprocess, 'run', timeout)
    monkeypatch.setattr(sys, 'argv', ['em_tick.py', '--config', str(harness.companion/'registry.json')])
    assert tick.main() != 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])['status'] == 'incomplete'

def test_legacy_topic_retry_keeps_its_original_mailbox_generation(harness, monkeypatch):
    harness.stored['value']['cursors'] = {FIX['account']['user']+'::INBOX': FIX['cursor']}
    harness.stored['value']['topic_retry'] = [copy.deepcopy(FIX['message'])]
    harness.fetched['records'] = []
    monkeypatch.setattr(tick.em_watch, 'run_once', lambda *a, **k:
                        ([], {**FIX['cursor'], 'uidvalidity': 8}))
    monkeypatch.setattr(tick.em_topic, 'load_config', lambda *a, **k:
                        {'taxonomy': 'Review', 'sender_map': {}, 'allowed_labels': ['Review']})
    monkeypatch.setattr(tick.em_topic, 'judge', lambda *a, **k:
                        {'state': 'decided', 'labels': [{'label': 'Review'}]})
    monkeypatch.setattr(tick, '_label_add', lambda *a, **k: ack(k['idempotency_key'], 'topic_label'))
    assert harness.run(topic_enabled=True)['status'] == 'completed'
    row = next(iter(harness.stored['value']['actions'].values()))
    assert row['uidvalidity'] == FIX['cursor']['uidvalidity']

# Keep a real persistence function reference before per-case adapter interception.
_REAL_SAVE_STATE = tick.em_watch.save_state


def test_state_writer_rejects_unversioned_destination_before_mkdir(tmp_path):
    destination = tmp_path/'unversioned'/'state.json'
    with pytest.raises(ValueError):
        _REAL_SAVE_STATE(str(destination), {'cursors': {}})
    assert not destination.parent.exists()


def test_real_private_state_checkpoint_survives_failed_replace(harness, monkeypatch):
    destination = harness.companion/'state'/'durable.json'
    _REAL_SAVE_STATE(str(destination), {'phase': 'uncertain'})
    before = destination.read_bytes()
    def fail(*a):
        raise OSError('synthetic atomic-replace failure')
    monkeypatch.setattr(tick.em_watch.os, 'replace', fail)
    with pytest.raises(OSError):
        _REAL_SAVE_STATE(str(destination), {'phase': 'completed'})
    assert destination.read_bytes() == before

@pytest.mark.parametrize('registry', [[], {'accounts': [None]}, {'daily_summary': ['invalid']}])
def test_doctor_malformed_registry_still_returns_json(harness, monkeypatch, capsys, registry):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]/'scripts'))
    import verify_config
    (harness.companion/'registry.json').write_text(json.dumps(registry))
    monkeypatch.setattr(sys, 'argv', ['verify_config.py', '--config-dir', str(harness.companion), '--json'])
    assert verify_config.main() != 0
    assert json.loads(capsys.readouterr().out)['status'] == 'not_ready'


@pytest.mark.parametrize('adapter', ['archive', 'topic_label'])
@pytest.mark.parametrize('returncode', [0, 1])
def test_helper_preserves_matching_non_delivery_receipt(monkeypatch, adapter, returncode):
    case = FIX['helper_contract']
    receipt = {'status': 'not_applied', 'idempotency_key': case['idempotency_key'],
               'adapter': adapter, 'evidence': case['evidence']}
    monkeypatch.setattr(tick.subprocess, 'run', lambda *a, **kw:
                        SimpleNamespace(returncode=returncode, stdout=json.dumps(receipt)))
    fn = tick.archive if adapter == 'archive' else tick._label_add
    assert fn(FIX['account']['user'], FIX['message']['message_id'], case['label'], False,
              idempotency_key=case['idempotency_key']) == receipt


@pytest.mark.parametrize('adapter', ['archive', 'topic_label'])
@pytest.mark.parametrize('invalid_field', ['evidence', 'idempotency_key', 'adapter'])
def test_helper_non_delivery_requires_matching_evidence(monkeypatch, adapter, invalid_field):
    case = FIX['helper_contract']
    receipt = {'status': 'not_applied', 'idempotency_key': case['idempotency_key'],
               'adapter': adapter, 'evidence': case['evidence']}
    receipt[invalid_field] = ''
    monkeypatch.setattr(tick.subprocess, 'run', lambda *a, **kw:
                        SimpleNamespace(returncode=0, stdout=json.dumps(receipt)))
    fn = tick.archive if adapter == 'archive' else tick._label_add
    assert fn(FIX['account']['user'], FIX['message']['message_id'], case['label'], False,
              idempotency_key=case['idempotency_key'])['status'] == 'uncertain'


_REAL_LOAD_STATE = tick.em_watch.load_state


@pytest.mark.parametrize('adapter', ['archive', 'topic_label'])
def test_real_helper_failure_checkpoint_retries_once(harness, monkeypatch, adapter):
    case = FIX['helper_contract']
    state_path = harness.companion/'state'/(FIX['account']['slug']+'.state.json')
    monkeypatch.setattr(tick.em_watch, 'load_state', _REAL_LOAD_STATE)
    monkeypatch.setattr(tick.em_watch, 'save_state', _REAL_SAVE_STATE)
    monkeypatch.setattr(tick, 'classify_records_parallel', lambda messages, *a:
                        [{**FIX['verdict'], 'priority': 'NOISE' if adapter == 'archive' else 'FYI'}
                         for _ in messages])
    monkeypatch.setattr(tick.em_topic, 'load_config', lambda *a, **kw:
                        {'taxonomy': case['label'], 'sender_map': {}, 'allowed_labels': [case['label']]})
    monkeypatch.setattr(tick.em_topic, 'judge', lambda *a, **kw:
                        {'state': 'decided', 'labels': [{'label': case['label']}]})
    original_run = tick.subprocess.run
    calls = []
    def helper(argv, **kwargs):
        if len(argv) < 2 or argv[1] != tick.LABEL_TOOL:
            return original_run(argv, **kwargs)
        key = argv[argv.index('--idempotency-key')+1]
        persisted = json.loads(state_path.read_text())
        assert persisted['actions'][key]['status'] == 'uncertain'
        calls.append(key)
        receipt = {'idempotency_key': key, 'adapter': adapter}
        if len(calls) == 1:
            receipt.update(status='not_applied', evidence=case['evidence'])
        else:
            receipt.update(status='confirmed', receipt_id=case['receipt_id'], matched=1, applied=True)
        return SimpleNamespace(returncode=0, stdout=json.dumps(receipt))
    monkeypatch.setattr(tick.subprocess, 'run', helper)
    assert harness.run(topic_enabled=adapter == 'topic_label')['status'] == 'incomplete'
    failed = next(iter(json.loads(state_path.read_text())['actions'].values()))
    assert failed['status'] == 'failed'
    assert failed['receipt']['evidence'] == case['evidence']
    harness.fetched['records'] = []
    assert harness.run(topic_enabled=adapter == 'topic_label')['status'] == 'completed'
    assert harness.run(topic_enabled=adapter == 'topic_label')['status'] == 'completed'
    assert len(calls) == 2 and calls[0] == calls[1]


@pytest.mark.parametrize('override', [False, True])
def test_tick_uses_one_selected_interpreter_without_dry_writes(harness, monkeypatch, capsys, override):
    case = FIX['helper_contract']
    cfg = _configured_registry(harness, monkeypatch)
    selected = str((harness.companion/case['selected_python']).resolve())
    cfg['runtime']['python'] = case['unavailable_python'] if override else selected
    registry = harness.companion/'registry.json'
    registry.write_text(json.dumps(cfg))
    probes, runtimes = [], []
    def probe(python):
        probes.append(python)
        return python == selected, 'synthetic selected-interpreter check'
    monkeypatch.setattr(tick.em_runtime, 'probe_interpreter', probe)
    monkeypatch.setattr(tick, 'preflight', lambda *a: [])
    monkeypatch.setattr(tick.em_pool, 'available', lambda *a: False)
    process = tick.process_account
    def capture(*args, **kwargs):
        runtimes.append(kwargs['runtime']['python'])
        return process(*args, **kwargs)
    monkeypatch.setattr(tick, 'process_account', capture)
    argv = ['em_tick.py', '--config', str(registry), '--dry']
    if override:
        argv += ['--python', selected]
    monkeypatch.setattr(sys, 'argv', argv)
    before = {str(p.relative_to(harness.companion)): p.read_bytes()
              for p in harness.companion.rglob('*') if p.is_file()}
    assert tick.main() == 0
    assert probes == runtimes == [selected]
    assert json.loads(capsys.readouterr().out.splitlines()[-1])['status'] == 'planned'
    after = {str(p.relative_to(harness.companion)): p.read_bytes()
             for p in harness.companion.rglob('*') if p.is_file()}
    assert after == before and not harness.saves and not harness.effects
