"""An uncertain daily-summary pool step is reconciled against the pool instead of stalling forever.

The alert step has no source of truth and is never replayed. Synthetic data only.
"""
import datetime
import json
import sys

import pytest

from test_durable_dispatch import harness, _configured_registry  # noqa: F401  (fixture)
import em_summary
import em_watch

REAL_LOAD, REAL_SAVE = em_watch.load_state, em_watch.save_state

EVENT = 'event-1'
STALE = '2026-01-02T13:00:00Z'


def _state(mark_done='uncertain', arm_next='pending', alert='completed'):
    receipt = {'status': 'confirmed', 'idempotency_key': 'run1:alert', 'adapter': 'alert', 'receipt_id': 'msg-1'}
    return {'summary_runs': {'run1': {'steps': [
        {'key': 'run1:alert', 'adapter': 'alert', 'status': alert,
         'payload': {'message': 'synthetic summary'}, 'receipt': receipt if alert == 'completed' else None},
        {'key': 'run1:summary_mark_done', 'adapter': 'summary_mark_done', 'status': mark_done,
         'payload': {'item_id': EVENT}, 'receipt': None},
        {'key': 'run1:summary_arm_next', 'adapter': 'summary_arm_next', 'status': arm_next,
         'payload': {'due_at': STALE}, 'receipt': None}]}}}


@pytest.fixture
def worker(harness, monkeypatch):
    _configured_registry(harness, monkeypatch)
    monkeypatch.setattr(em_watch, 'load_state', REAL_LOAD)
    monkeypatch.setattr(em_watch, 'save_state', REAL_SAVE)
    path = harness.companion / 'data' / 'state' / 'summary.state.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    pool = {'state': 'cancelled', 'items': [], 'calls': []}

    def run(reminder, db, verb, args, **kwargs):
        pool['calls'].append(verb)
        if verb == 'get':
            if pool['state'] is None:
                raise OSError('synthetic unreadable pool')
            return {'item': {'id': args[args.index('--id') + 1], 'state': pool['state']}}
        if verb == 'add':
            return {'item': {'id': 'event-2', 'idempotency_key': args[args.index('--idempotency-key') + 1],
                             'due_at': args[args.index('--due-at') + 1]}}
        raise AssertionError('unexpected pool verb ' + verb)

    def mark_done(reminder, db, item_id, **kwargs):
        pool['calls'].append('done')
        return {'id': item_id, 'state': 'done'}
    monkeypatch.setattr(em_summary.em_pool, '_run', run)
    monkeypatch.setattr(em_summary.em_pool, 'mark_done', mark_done)
    monkeypatch.setattr(em_summary.em_pool, 'list_items', lambda *a, **k: list(pool['items']))
    monkeypatch.setattr(em_summary.em_pool, 'due', lambda *a, **k: {'items': []})
    monkeypatch.setattr(em_summary.em_alert, 'send', lambda *a, **k: pytest.fail('a summary alert was replayed'))
    monkeypatch.setattr(sys, 'argv', ['em_summary.py', '--config', str(harness.companion / 'registry.json')])

    def steps():
        return {s['adapter']: s for s in json.loads(path.read_text(encoding='utf-8'))['summary_runs']['run1']['steps']}
    return pool, path, steps


def test_a_mark_done_on_an_event_the_pool_already_closed_completes_and_the_next_event_is_armed(worker):
    pool, path, steps = worker
    path.write_text(json.dumps(_state()), encoding='utf-8')
    assert em_summary.main() == 0, 'the summary stays stuck on an uncertain mark_done'
    done, arm = steps()['summary_mark_done'], steps()['summary_arm_next']
    assert done['status'] == 'completed' and done['receipt']['receipt_id'] == EVENT + ':cancelled'
    assert 'done' not in pool['calls'], 'a closed event is not transitioned again'
    assert arm['status'] == 'completed'
    armed = datetime.datetime.fromisoformat(arm['payload']['due_at'].replace('Z', '+00:00'))
    assert armed > datetime.datetime.now(datetime.timezone.utc), 'a missed summary slot was armed (it would fire at once)'


def test_a_mark_done_whose_event_is_still_open_is_retried(worker):
    pool, path, steps = worker
    pool['state'] = 'pending'
    path.write_text(json.dumps(_state()), encoding='utf-8')
    assert em_summary.main() == 0
    assert pool['calls'].count('done') == 1
    assert steps()['summary_mark_done']['status'] == 'completed'


def test_an_uncertain_arm_next_found_in_the_pool_is_not_added_again(worker):
    pool, path, steps = worker
    pool['items'] = [{'id': 'event-2', 'idempotency_key': 'run1:summary_arm_next', 'due_at': STALE}]
    state = _state(mark_done='completed', arm_next='uncertain')
    state['summary_runs']['run1']['steps'][1]['receipt'] = {
        'status': 'confirmed', 'idempotency_key': 'run1:summary_mark_done', 'adapter': 'summary_mark_done',
        'receipt_id': EVENT}
    path.write_text(json.dumps(state), encoding='utf-8')
    assert em_summary.main() == 0
    assert 'add' not in pool['calls']
    assert steps()['summary_arm_next']['receipt']['receipt_id'] == 'event-2'


def test_an_unreadable_pool_leaves_the_step_uncertain(worker):
    pool, path, steps = worker
    pool['state'] = None
    path.write_text(json.dumps(_state()), encoding='utf-8')
    assert em_summary.main() != 0
    assert steps()['summary_mark_done']['status'] == 'uncertain' and 'done' not in pool['calls']


def test_an_uncertain_summary_alert_is_never_replayed(worker):
    pool, path, steps = worker
    path.write_text(json.dumps(_state(alert='uncertain', mark_done='pending')), encoding='utf-8')
    assert em_summary.main() != 0
    assert steps()['alert']['status'] == 'uncertain' and not pool['calls']
