"""A tick saves its work chunk by chunk, so a tick stopped part way keeps what it finished.

The scheduled task stops a tick after one hour. A tick that only saved after its whole batch
lost every classification it had made, and the next tick started the same batch again. These
tests stop a tick at chosen points (an exception that process_account does not catch, like the
process ending) and check what the state file holds and what the next tick does. Synthetic
data only.
"""
import copy
import datetime
import email.utils
from pathlib import Path
import sys

import pytest

from test_durable_dispatch import FIX, harness  # noqa: F401  (fixture)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import em_actions
import em_tick as tick

ACCOUNT = {**FIX['account'], 'monitored_folders': ['INBOX']}
GENERATION = 7
FIRST_UID = 101


class Killed(BaseException):
    """Stands in for the process ending: process_account catches Exception, not this."""


def _message(n):
    sent = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)
    return {'uid': FIRST_UID + n, 'gm_msgid': str(5000 + n), 'message_id': '<chunk%d@example.com>' % n,
            'thread_key': 'thread-%d' % n, 'from': 'AcmeCorp Billing <billing@example-employer.com>',
            'subject': 'Synthetic notice %d' % n, 'body': 'Synthetic body.',
            'date': email.utils.format_datetime(sent), 'list_unsubscribe': False}


@pytest.fixture
def mailbox(harness, monkeypatch):
    """A mailbox of 12 new messages served like IMAP: UIDs after the cursor, max_batch at a time."""
    messages = [_message(n) for n in range(12)]
    harness.stored['value'] = {'cursors': {FIX['account']['user'] + '::INBOX': {
        'uidvalidity': GENERATION, 'last_uid': FIRST_UID - 1}}}

    def run_once(user, folder, cursor, max_batch=400, app_pw=None, info=None):
        assert cursor['uidvalidity'] == GENERATION
        due = [m for m in messages if m['uid'] > cursor['last_uid']][:max_batch]
        if isinstance(info, dict):
            info['caught_up'] = len(due) == len([m for m in messages if m['uid'] > cursor['last_uid']])
        last = due[-1]['uid'] if due else cursor['last_uid']
        # Like a real FETCH, the answer is not guaranteed to come back in UID order.
        return copy.deepcopy(list(reversed(due))), {'uidvalidity': GENERATION, 'last_uid': last}
    monkeypatch.setattr(tick.em_watch, 'run_once', run_once)
    return messages


def _classifier(monkeypatch, kill_at=None):
    """Every message ACTION (one alert each); raise Killed when message `kill_at` is classified."""
    calls = []

    def classify(msgs, *a):
        subjects = [m['subject'] for m in msgs]
        calls.append(subjects)
        if kill_at is not None and 'Synthetic notice %d' % kill_at in subjects:
            raise Killed()
        return [copy.deepcopy(FIX['verdict']) for _ in msgs]
    monkeypatch.setattr(tick, 'classify_records_parallel', classify)
    return calls


def _relay(monkeypatch, harness, kill_on=None):
    sent = []

    def send(message, idempotency_key=None, **kw):
        # The row is durably uncertain before every send, as before.
        assert harness.stored['value']['actions'][idempotency_key]['status'] == 'uncertain'
        sent.append(idempotency_key)
        if idempotency_key == kill_on:
            raise Killed()
        return {'status': 'confirmed', 'idempotency_key': idempotency_key, 'adapter': 'alert',
                'receipt_id': 'receipt-%d' % len(sent)}
    monkeypatch.setattr(tick.em_alert, 'send', send)
    return sent


def _run(harness, **kw):
    return tick.process_account(copy.deepcopy(ACCOUNT), {}, 'absent', None, None,
                                str(harness.companion / 'state'), False,
                                agent_cfg={'mode': 'heuristic'}, pool_enabled=False, **kw)


def _identity(message):
    return em_actions.identity(FIX['account']['user'], 'INBOX', GENERATION, message['message_id'])


def _alert_key(message):
    return em_actions.identity(FIX['account']['user'], 'INBOX', GENERATION, message['message_id'], 'alert')


def _cursor(harness):
    return harness.stored['value']['cursors'][FIX['account']['user'] + '::INBOX']['last_uid']


def test_a_tick_killed_mid_batch_keeps_the_chunks_it_finished(harness, monkeypatch, mailbox):
    assert getattr(tick, 'SAVE_EVERY', 5) == 5   # the chunk boundaries below assume five
    _classifier(monkeypatch, kill_at=10)      # the third chunk (messages 10, 11) never finishes
    sent = _relay(monkeypatch, harness)
    with pytest.raises(Killed):
        _run(harness)
    saved = harness.stored['value']
    assert _cursor(harness) == mailbox[9]['uid'], 'the cursor did not keep the finished prefix'
    assert set(saved['observed_messages']) == {_identity(m) for m in mailbox[:10]}
    assert {row['message_id'] for row in saved['actions'].values()} == {m['message_id'] for m in mailbox[:10]}
    # Each finished chunk was also delivered before the next one was classified.
    assert sent == [_alert_key(m) for m in mailbox[:10]]
    assert all(row['status'] == 'completed' for row in saved['actions'].values())


def test_the_next_tick_resumes_after_the_prefix_and_resends_nothing(harness, monkeypatch, mailbox):
    _classifier(monkeypatch, kill_at=10)
    sent = _relay(monkeypatch, harness)
    with pytest.raises(Killed):
        _run(harness)
    calls = _classifier(monkeypatch)
    result = _run(harness)
    assert result['status'] == 'completed' and result['new'] == 2
    assert calls == [['Synthetic notice 10', 'Synthetic notice 11']], 'finished messages were classified again'
    assert sent == [_alert_key(m) for m in mailbox], 'an alert was sent twice or not at all'
    assert _cursor(harness) == mailbox[-1]['uid']


def test_a_kill_during_an_alert_never_resends_it(harness, monkeypatch, mailbox):
    _classifier(monkeypatch)
    sent = _relay(monkeypatch, harness, kill_on=_alert_key(mailbox[6]))
    with pytest.raises(Killed):
        _run(harness)
    saved = harness.stored['value']
    assert saved['actions'][_alert_key(mailbox[6])]['status'] == 'uncertain'
    # The second chunk was saved (cursor just after it) before its first alert went out.
    assert _cursor(harness) == mailbox[9]['uid']
    sent2 = _relay(monkeypatch, harness)
    result = _run(harness)
    assert _alert_key(mailbox[6]) not in sent2, 'an alert whose send was interrupted was sent again'
    assert sent2 == [_alert_key(m) for m in mailbox[7:]]
    assert set(sent) & set(sent2) == set()
    assert result['status'] == 'incomplete' and result['pending'] == 1   # the one uncertain alert


def test_a_tick_past_its_time_budget_starts_no_new_chunk(harness, monkeypatch, mailbox):
    clock = {'now': 0.0}
    monkeypatch.setattr(tick.time, 'monotonic', lambda: clock['now'])
    calls = _classifier(monkeypatch)
    real = tick.classify_records_parallel

    def slow(msgs, *a):
        clock['now'] += 600.0    # ten minutes of model time per chunk
        return real(msgs, *a)
    monkeypatch.setattr(tick, 'classify_records_parallel', slow)
    sent = _relay(monkeypatch, harness)
    result = _run(harness, deadline=900.0)
    assert result['stopped_early'] is True and result['new'] == 10
    assert len(calls) == 2, 'a chunk was started after the time budget was spent'
    assert _cursor(harness) == mailbox[9]['uid']
    assert sent == [_alert_key(m) for m in mailbox[:10]]
    assert _identity(mailbox[10]) not in harness.stored['value']['observed_messages']


def test_a_duplicate_seen_in_a_finished_chunk_is_not_handled_again(harness, monkeypatch, mailbox):
    # The same Message-ID twice in one fetch (Gmail can show a message under two UIDs).
    mailbox[3]['message_id'] = mailbox[2]['message_id']
    _classifier(monkeypatch, kill_at=11)
    sent = _relay(monkeypatch, harness)
    with pytest.raises(Killed):
        _run(harness)
    _classifier(monkeypatch)
    _run(harness)
    assert len(sent) == len(set(sent)) == 11


def test_the_topic_retry_queue_is_judged_once_per_tick_not_once_per_chunk(harness, monkeypatch, mailbox):
    judged = []
    monkeypatch.setattr(tick.em_topic, 'load_config', lambda *a, **k: {
        'taxonomy': {}, 'sender_map': {}, 'allowed_labels': [], 'type_labels': []})
    monkeypatch.setattr(tick, '_make_transport', lambda **k: None)

    def judge(message, *a, **k):
        judged.append(message['subject'])
        if message['subject'].startswith('Queued'):
            return {'state': 'failed', 'labels': []}
        return {'state': 'unsure', 'labels': []}
    monkeypatch.setattr(tick.em_topic, 'judge', judge)
    queued = [{'from': 'news@example.com', 'subject': 'Queued %d' % n, 'date': '', 'list_id': '',
               'mailbox': 'INBOX', 'uidvalidity': GENERATION, 'message_id': '<queued%d@example.com>' % n}
              for n in range(2)]
    harness.stored['value']['topic_retry'] = copy.deepcopy(queued)
    _classifier(monkeypatch)
    _relay(monkeypatch, harness)
    result = tick.process_account(copy.deepcopy(ACCOUNT), {}, 'absent', None, None,
                                  str(harness.companion / 'state'), False, agent_cfg={'mode': 'heuristic'},
                                  pool_enabled=False, topic_enabled=True)
    assert sorted(judged) == sorted(['Queued 0', 'Queued 1'] + [m['subject'] for m in mailbox])
    assert [e['message_id'] for e in harness.stored['value']['topic_retry']] == [e['message_id'] for e in queued]
    assert result['pending'] == 2


def test_a_refused_alert_is_retried_by_the_next_tick_not_by_the_next_chunk(harness, monkeypatch, mailbox):
    """A relay refusal (not_applied) in the first chunk is answered by the next tick.

    Three things together: the refused alert goes out with its own chunk, before the second chunk
    is classified (chunked dispatch); the later chunks of the same tick do not send it again (each
    open row is visited once per tick); and the next tick sends it exactly once more, which this
    time the relay accepts, so that tick completes with nothing left open.
    """
    events = []
    _classifier(monkeypatch)
    classify = tick.classify_records_parallel

    def logged_classify(msgs, *a):
        events.append(('classify', tuple(m['subject'] for m in msgs)))
        return classify(msgs, *a)
    monkeypatch.setattr(tick, 'classify_records_parallel', logged_classify)
    refused = _alert_key(mailbox[0])
    relay = {'refuse': True}
    sent = []

    def send(message, idempotency_key=None, **kw):
        assert harness.stored['value']['actions'][idempotency_key]['status'] == 'uncertain'
        sent.append(idempotency_key)
        events.append(('send', idempotency_key))
        if idempotency_key == refused and relay['refuse']:
            return {'status': 'not_applied', 'idempotency_key': idempotency_key, 'adapter': 'alert',
                    'evidence': 'synthetic relay refused before sending'}
        return {'status': 'confirmed', 'idempotency_key': idempotency_key, 'adapter': 'alert',
                'receipt_id': 'receipt-%d' % len(sent)}
    monkeypatch.setattr(tick.em_alert, 'send', send)

    first = _run(harness)
    assert first['status'] == 'incomplete' and first['pending'] == 1
    assert harness.stored['value']['actions'][refused]['status'] == 'failed'
    first_send = events.index(('send', refused))
    second_chunk = next(i for i, e in enumerate(events)
                        if e[0] == 'classify' and 'Synthetic notice 5' in e[1])
    assert first_send < second_chunk, 'the first chunk was not delivered before the next chunk was read'
    assert sent.count(refused) == 1, 'a later chunk in the same tick dispatched the refused alert again'
    assert len(sent) == 12

    relay['refuse'] = False
    second = _run(harness)
    assert sent.count(refused) == 2, 'the next tick did not retry the refused alert exactly once'
    assert sent[12:] == [refused] and second['new'] == 0
    assert second['status'] == 'completed' and second['pending'] == 0
    assert harness.stored['value']['actions'][refused]['status'] == 'completed'


def _topics(monkeypatch, clock, cost):
    """Topic judging enabled; each judgement takes `cost` seconds of the fake clock."""
    judged = []
    monkeypatch.setattr(tick.em_topic, 'load_config', lambda *a, **k: {
        'taxonomy': {}, 'sender_map': {}, 'allowed_labels': [], 'type_labels': []})
    monkeypatch.setattr(tick, '_make_transport', lambda **k: None)

    def judge(message, *a, **k):
        judged.append(message['subject'])
        clock['now'] += cost
        return {'state': 'unsure', 'labels': []}
    monkeypatch.setattr(tick.em_topic, 'judge', judge)
    return judged


def test_account_deadline_is_an_equal_share_of_what_is_left():
    assert tick.account_deadline(2400.0, 3, now=0.0) == 800.0
    assert tick.account_deadline(2400.0, 2, now=1000.0) == 1700.0
    assert tick.account_deadline(2400.0, 1, now=2000.0) == 2400.0   # the last account: the tick's own
    assert tick.account_deadline(2400.0, 2, now=2500.0) == 2500.0   # already past: no new work at all


def test_a_backlogged_account_cannot_starve_the_accounts_after_it(harness, monkeypatch):
    """Three accounts, run in order inside one 40 minute tick; the first has a 40 message backlog.

    Every message costs 70 s (one topic judgement). With one deadline for the whole tick the first
    account would use all of it and the other two would read nothing, every tick. With a fair share
    each, the first stops after its share and the other two read all their new mail.
    """
    clock = {'now': 0.0}
    monkeypatch.setattr(tick.time, 'monotonic', lambda: clock['now'])
    judged = _topics(monkeypatch, clock, 70.0)
    users = ['user%d@example.com' % n for n in (1, 2, 3)]
    mail = {users[0]: [_message(n) for n in range(40)],
            users[1]: [_message(100 + n) for n in range(3)],
            users[2]: [_message(200 + n) for n in range(3)]}
    for user, messages in mail.items():
        for m in messages:
            m['message_id'] = '<%s-%s' % (user.split('@')[0], m['message_id'][1:])
    states = {}

    def load(path):
        return states.setdefault(path, {'cursors': {user + '::INBOX': {'uidvalidity': GENERATION,
                                                                       'last_uid': FIRST_UID - 1}
                                                    for user in users}})

    def save(path, value):
        states[path] = copy.deepcopy(value)
    monkeypatch.setattr(tick.em_watch, 'load_state', load)
    monkeypatch.setattr(tick.em_watch, 'save_state', save)

    def run_once(user, folder, cursor, max_batch=400, app_pw=None, info=None):
        due = [m for m in mail[user] if m['uid'] > cursor['last_uid']][:max_batch]
        if isinstance(info, dict):
            info['caught_up'] = len(due) == len([m for m in mail[user] if m['uid'] > cursor['last_uid']])
        last = due[-1]['uid'] if due else cursor['last_uid']
        return copy.deepcopy(due), {'uidvalidity': GENERATION, 'last_uid': last}
    monkeypatch.setattr(tick.em_watch, 'run_once', run_once)
    _classifier(monkeypatch)
    monkeypatch.setattr(tick.em_alert, 'send', lambda message, idempotency_key=None, **kw: {
        'status': 'confirmed', 'idempotency_key': idempotency_key, 'adapter': 'alert', 'receipt_id': 'r'})
    accounts = [{'slug': user.split('@')[0], 'user': user, 'monitored_folders': ['INBOX'], 'max_batch': 400}
                for user in users]

    def run_one(acct, deadline):
        return tick.process_account(acct, {}, 'absent', None, None, str(harness.companion / 'state'), False,
                                    agent_cfg={'mode': 'heuristic'}, pool_enabled=False, topic_enabled=True,
                                    deadline=deadline)
    results = tick.run_accounts(accounts, run_one, 2400.0, [])
    first, second, third = results
    assert first['stopped_early'] is True and 0 < first['new'] < 40
    assert second['new'] == 3 and not second.get('stopped_early'), 'the second account was starved'
    assert third['new'] == 3 and not third.get('stopped_early'), 'the third account was starved'
    assert {'Synthetic notice %d' % n for n in (100, 101, 102, 200, 201, 202)} <= set(judged)
    # The whole tick still ends inside its budget plus at most one judgement.
    assert clock['now'] <= 2400.0 + 70.0


def test_the_topic_retry_pass_stops_at_the_deadline_and_keeps_the_rest_queued(harness, monkeypatch, mailbox):
    clock = {'now': 0.0}
    monkeypatch.setattr(tick.time, 'monotonic', lambda: clock['now'])
    judged = _topics(monkeypatch, clock, 60.0)
    queued = [{'from': 'news@example.com', 'subject': 'Queued %d' % n, 'date': '', 'list_id': '',
               'mailbox': 'INBOX', 'uidvalidity': GENERATION, 'message_id': '<queued%d@example.com>' % n}
              for n in range(6)]
    harness.stored['value']['topic_retry'] = copy.deepcopy(queued)
    calls = _classifier(monkeypatch)
    _relay(monkeypatch, harness)
    result = _run(harness, topic_enabled=True, deadline=150.0)
    assert judged == ['Queued 0', 'Queued 1', 'Queued 2'], 'the retry pass ignored the deadline'
    assert [e['message_id'] for e in harness.stored['value']['topic_retry']] == \
        [e['message_id'] for e in queued[3:]], 'the entries not reached were not kept in order'
    assert calls == [] and result['stopped_early'] is True and result['new'] == 0
    assert _cursor(harness) == FIRST_UID - 1


def test_a_chunk_that_reaches_the_deadline_queues_its_unjudged_topics(harness, monkeypatch, mailbox):
    clock = {'now': 0.0}
    monkeypatch.setattr(tick.time, 'monotonic', lambda: clock['now'])
    judged = _topics(monkeypatch, clock, 60.0)
    calls = _classifier(monkeypatch)
    sent = _relay(monkeypatch, harness)
    result = _run(harness, topic_enabled=True, deadline=130.0)
    assert judged == ['Synthetic notice %d' % n for n in range(3)], 'a judgement started after the deadline'
    saved = harness.stored['value']
    assert [e['message_id'] for e in saved['topic_retry']] == [m['message_id'] for m in mailbox[3:5]]
    # The chunk itself is saved and delivered; nothing after it was read.
    assert len(calls) == 1 and _cursor(harness) == mailbox[4]['uid'] and result['new'] == 5
    assert sent == [_alert_key(m) for m in mailbox[:5]]
    assert result['stopped_early'] is True
