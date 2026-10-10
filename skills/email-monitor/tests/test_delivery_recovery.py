"""Regressions for a state in which no keyed action could ever complete.

Three independent causes, each pinned here with synthetic data:
  * one pool item's defective reviewed rule made every pool write raise ERR_MERGE_RULE;
  * an uncertain pool action stayed uncertain forever although the pool can prove its disposition;
  * keyed alert and label calls passed arguments their helpers reject, so nothing was ever sent.
"""
import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import em_actions
import em_alert
import em_pool
import em_tick as tick
from test_durable_dispatch import FIX, harness  # noqa: F401  (fixture)

spec = importlib.util.spec_from_file_location('recovery_fixtures', SCRIPTS.parents[2] / 'tools/make_fixtures.py')
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


def _defective_item():
    item = fixtures.pool_merge_case()
    item['ext']['x_email_monitor_merge_rules'][0]['contains'] = []
    return item


def _pool(monkeypatch, rows):
    calls = []

    def run(reminder, db, verb, args, **kwargs):
        calls.append(verb)
        if verb == 'list':
            return {'items': copy.deepcopy(rows)}
        if verb == 'add':
            ext = json.loads(args[args.index('--ext') + 1])
            item = {'id': 'created-%d' % len(rows), 'state': 'pending', 'ext': ext}
            rows.append(item)
            return {'item': copy.deepcopy(item)}
        raise AssertionError(verb)
    monkeypatch.setattr(em_pool, '_run', run)
    return calls


def test_defective_rule_on_an_unrelated_item_does_not_block_other_mail(monkeypatch, tmp_path):
    rows = [_defective_item()]
    calls = _pool(monkeypatch, rows)
    receipt = em_pool.upsert('synthetic-cli', str(tmp_path / 'db'), '<other@example.com>', 'thread-z',
                             'Unrelated action', ext_extra={'account': 'user1', 'from': 'news@example.com',
                                                            'subject_raw': 'Weekly update'},
                             idempotency_key='synthetic-key-1')
    assert calls == ['list', 'add']
    assert em_actions.receipt_status(receipt, 'synthetic-key-1', 'pool') == 'confirmed'


def test_defective_rule_still_fails_closed_for_the_mail_it_names(monkeypatch, tmp_path):
    rows = [_defective_item()]
    calls = _pool(monkeypatch, rows)
    with pytest.raises(em_pool.PoolError, match='ERR_MERGE_RULE'):
        em_pool.upsert('synthetic-cli', str(tmp_path / 'db'), '<named@example.com>', 'thread-y',
                       'Named action', ext_extra={'account': 'user1', 'from': 'Billing <billing@example.com>',
                                                  'subject_raw': 'Payment Reminder'},
                       idempotency_key='synthetic-key-2')
    assert calls == ['list']


def test_pool_reconciliation_reads_the_key_from_the_pool():
    row = {'idempotency_key': 'synthetic-key-3', 'action': 'pool'}
    item = fixtures.pool_merge_case()
    assert em_pool.reconcile(row, [item])['status'] == 'not_applied'
    item['ext']['x_email_monitor_action_keys'] = ['synthetic-key-3']
    receipt = em_pool.reconcile(row, [item])
    assert em_actions.receipt_status(receipt, 'synthetic-key-3', 'pool') == 'confirmed'
    assert receipt['receipt_id'] == item['id']
    assert em_actions.receipt_status(em_pool.reconcile(row, []), 'synthetic-key-3', 'pool') == 'not_applied'


def _run_with_pool(harness):
    return tick.process_account(copy.deepcopy(FIX['account']), {}, 'synthetic-cli', None, None,
                                str(harness.companion / 'state'), False, agent_cfg={'mode': 'heuristic'},
                                pool_enabled=True)


def _pool_row(harness):
    return next(row for row in harness.stored['value']['actions'].values() if row['action'] == 'pool')


def test_uncertain_pool_action_is_retried_once_the_pool_proves_it_absent(harness, monkeypatch):
    writes = []

    def upsert(*a, **kw):
        writes.append(kw['idempotency_key'])
        if len(writes) == 1:
            raise em_pool.PoolError('ERR_MERGE_RULE', 'synthetic first failure')
        return {'status': 'confirmed', 'idempotency_key': kw['idempotency_key'], 'adapter': 'pool',
                'receipt_id': 'synthetic-item'}
    monkeypatch.setattr(em_pool, 'upsert', upsert)
    listings = []
    monkeypatch.setattr(em_pool, 'list_items', lambda *a, **kw: listings.append(1) or [])
    assert _run_with_pool(harness)['status'] == 'incomplete'
    assert _pool_row(harness)['status'] == 'uncertain' and len(writes) == 1
    harness.fetched['records'] = []
    assert _run_with_pool(harness)['status'] == 'completed'
    assert len(writes) == 2 and writes[0] == writes[1] and listings == [1]
    assert _pool_row(harness)['status'] == 'completed'


def test_uncertain_pool_action_found_in_the_pool_completes_without_a_write(harness, monkeypatch):
    writes = []
    monkeypatch.setattr(em_pool, 'upsert', lambda *a, **kw: writes.append(1) or None)
    assert _run_with_pool(harness)['status'] == 'incomplete'
    key = _pool_row(harness)['idempotency_key']
    item = {'id': 'retained', 'ext': {'x_email_monitor_action_key': key}}
    monkeypatch.setattr(em_pool, 'list_items', lambda *a, **kw: [item])
    harness.fetched['records'] = []
    assert _run_with_pool(harness)['status'] == 'completed'
    assert writes == [1] and _pool_row(harness)['receipt']['receipt_id'] == 'retained'


def test_keyed_alert_passes_arguments_the_relay_accepts(monkeypatch):
    seen = {}

    def run(args, **kw):
        seen['args'] = args
        return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps({
            'status': 'confirmed', 'idempotency_key': 'synthetic-key-4', 'adapter': 'alert',
            'receipt_id': 'synthetic-message'}))
    monkeypatch.setattr(em_alert, '_egress_cmd', lambda: [sys.executable, 'relay.py', 'send', '--stream', 'mail', '--text'])
    monkeypatch.setattr(em_alert.subprocess, 'run', run)
    receipt = em_alert.send('Synthetic alert', idempotency_key='synthetic-key-4')
    args = seen['args']
    assert '--json' not in args
    assert args[args.index('--idempotency-key') + 1] == 'synthetic-key-4'
    assert args[args.index('--receipt-adapter') + 1] == 'alert'
    assert em_actions.receipt_status(receipt, 'synthetic-key-4', 'alert') == 'confirmed'


def test_keyed_label_names_its_adapter_instead_of_a_bare_json_flag(monkeypatch):
    seen = {}

    def run(args, **kw):
        seen['args'] = args
        return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps({
            'status': 'confirmed', 'idempotency_key': 'synthetic-key-5', 'adapter': 'topic_label',
            'receipt_id': 'synthetic-store', 'matched': 1, 'applied': True}))
    monkeypatch.setattr(tick.subprocess, 'run', run)
    receipt = tick._label_add('user1@example.com', '<m@example.com>', 'EM/Topic', False,
                              app_pw='synthetic-auth', idempotency_key='synthetic-key-5')
    args = seen['args']
    assert '--json' not in args
    assert args[args.index('--receipt-adapter') + 1] == 'topic_label'
    assert em_actions.receipt_status(receipt, 'synthetic-key-5', 'topic_label') == 'confirmed'
