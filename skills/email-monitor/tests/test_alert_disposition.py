"""Keep relay disposition intact through real keyed callers and disk checkpoints."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from test_durable_dispatch import FIX, harness, _configured_registry
import em_actions
import em_alert
import em_summary
import em_tick as tick
import em_watch

CASES = json.loads((Path(__file__).parent / 'alert_disposition.json').read_text(encoding='utf-8'))
REAL_SEND = em_alert.send
REAL_LOAD, REAL_SAVE = em_watch.load_state, em_watch.save_state


def helper_result(case, key):
    if 'raw_stdout' in case:
        stdout = case['raw_stdout']
    else:
        receipt = copy.deepcopy(case['response'])
        if isinstance(receipt, dict):
            receipt.setdefault('idempotency_key', key)
        stdout = json.dumps(receipt)
    return SimpleNamespace(returncode=case['returncode'], stdout=stdout, stderr='synthetic relay status')


@pytest.mark.parametrize('case', CASES['cases'])
def test_keyed_alert_preserves_only_proven_disposition(monkeypatch, case):
    monkeypatch.setattr(em_alert, '_egress_cmd', lambda: [sys.executable, 'synthetic-relay.py'])
    monkeypatch.setattr(em_alert.subprocess, 'run', lambda *a, **kw: helper_result(case, CASES['key']))
    try:
        receipt = REAL_SEND(CASES['message'], idempotency_key=CASES['key'])
    except RuntimeError:
        receipt = None
    assert em_actions.receipt_status(receipt, CASES['key'], 'alert') == case['disposition']
    if case['disposition'] == 'not_applied':
        assert receipt['evidence'] == case['response']['evidence']


@pytest.fixture
def caller(harness, monkeypatch, request):
    """Intercept only mailbox/model/helper effects; retain real adapter and state I/O."""
    kind = request.param
    monkeypatch.setattr(em_watch, 'load_state', REAL_LOAD)
    monkeypatch.setattr(em_watch, 'save_state', REAL_SAVE)
    monkeypatch.setattr(em_alert, 'send', REAL_SEND)
    relay = harness.companion / 'synthetic-relay.py'
    monkeypatch.setattr(em_alert, '_egress_cmd', lambda: [sys.executable, str(relay)])
    if kind == 'account':
        state_path = harness.companion / 'state' / (FIX['account']['slug'] + '.state.json')
        def run():
            result = harness.run()
            return 0 if result['status'] == 'completed' else 1
        def steps(state):
            return list(state['actions'].values())
    else:
        _configured_registry(harness, monkeypatch)
        state_path = harness.companion / 'data' / 'state' / 'summary.state.json'
        monkeypatch.setattr(em_summary.em_pool, 'due', lambda *a: {'items': [
            {'id': CASES['event_id'], 'ext': {'x_email_monitor_kind': 'daily-summary'}}]})
        monkeypatch.setattr(em_summary, 'assemble', lambda *a: CASES['message'])
        monkeypatch.setattr(sys, 'argv', ['em_summary.py', '--config', str(harness.companion / 'registry.json')])
        run = em_summary.main
        def steps(state):
            return next(iter(state['summary_runs'].values()))['steps']

    effects = []
    def mark_done(reminder, db, item_id):
        assert steps(json.loads(state_path.read_text(encoding='utf-8')))[0]['status'] == 'completed'
        effects.append('mark_done')
        return {'id': item_id, 'state': 'done'}
    def arm_next(reminder, db, verb, args):
        assert steps(json.loads(state_path.read_text(encoding='utf-8')))[1]['status'] == 'completed'
        effects.append('arm_next')
        assert verb == 'add'
        return {'item': {'id': CASES['event_id'] + '-next',
                         'idempotency_key': args[args.index('--idempotency-key') + 1],
                         'due_at': args[args.index('--due-at') + 1]}}
    monkeypatch.setattr(em_summary.em_pool, 'mark_done', mark_done)
    monkeypatch.setattr(em_summary.em_pool, '_run', arm_next)
    original_run = em_alert.subprocess.run
    requests, replies = [], []
    def helper(args, **kwargs):
        if len(args) < 2 or args[1] != str(relay):
            return original_run(args, **kwargs)
        key = args[args.index('--idempotency-key') + 1]
        assert steps(json.loads(state_path.read_text(encoding='utf-8')))[0]['status'] == 'uncertain'
        requests.append(key)
        assert replies, 'unexpected helper replay'
        return helper_result(replies.pop(0), key)
    monkeypatch.setattr(em_alert.subprocess, 'run', helper)
    return SimpleNamespace(run=run, steps=lambda: steps(json.loads(state_path.read_text(encoding='utf-8'))),
                           requests=requests, replies=replies, effects=effects, kind=kind,
                           fetched=harness.fetched, state_path=state_path)


@pytest.mark.parametrize('caller', ['account', 'summary'], indirect=True)
@pytest.mark.parametrize('case', [c for c in CASES['cases'] if c['disposition'] == 'not_applied'])
def test_caller_retries_proven_non_delivery_and_preserves_receipts(caller, case):
    caller.replies.extend([case, CASES['cases'][2]])
    assert caller.run() == 1
    failed = caller.steps()[0]
    assert failed['status'] == 'failed'
    assert failed['receipt']['evidence'] == case['response']['evidence']
    assert not caller.effects
    caller.fetched['records'] = []
    assert caller.run() == 0
    assert all(step['status'] == 'completed' for step in caller.steps())
    assert caller.steps()[0]['receipt']['receipt_id'] == CASES['cases'][2]['response']['receipt_id']
    confirmed_bytes = caller.state_path.read_bytes()
    assert caller.run() == 0
    assert caller.state_path.read_bytes() == confirmed_bytes
    assert len(caller.requests) == 2 and caller.requests[0] == caller.requests[1]
    assert caller.effects == (['mark_done', 'arm_next'] if caller.kind == 'summary' else [])


@pytest.mark.parametrize('caller', ['account', 'summary'], indirect=True)
@pytest.mark.parametrize('case', [c for c in CASES['cases'] if c['disposition'] == 'uncertain'])
def test_caller_does_not_replay_unknown_or_disputed_delivery(caller, case):
    caller.replies.append(case)
    assert caller.run() == 1
    assert caller.steps()[0]['status'] == 'uncertain'
    caller.fetched['records'] = []
    assert caller.run() == 1
    assert len(caller.requests) == 1 and not caller.effects


@pytest.mark.parametrize('returncode', [0, 27])
def test_unkeyed_alert_retains_legacy_success_and_error_api(monkeypatch, returncode):
    monkeypatch.setattr(em_alert, '_egress_cmd', lambda: [sys.executable, 'synthetic-relay.py'])
    monkeypatch.setattr(em_alert.subprocess, 'run', lambda *a, **kw: helper_result(
        {**CASES['cases'][0], 'returncode': returncode}, CASES['key']))
    if returncode:
        with pytest.raises(RuntimeError):
            REAL_SEND(CASES['message'])
    else:
        assert REAL_SEND(CASES['message']) is True
