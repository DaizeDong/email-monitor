"""Catch-up delivery of undelivered alerts and requeue of never-applied idempotent actions.

Synthetic data only.
"""
import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import em_actions
import em_catchup as cu

ACCOUNT = 'user1@example.com'


def _row(mid, action='alert', status='uncertain', message='', label=''):
    payload = {'message': message} if action == 'alert' else {'label': label}
    row = em_actions.new_action(ACCOUNT, 'INBOX', 7, {'message_id': mid}, action, payload)
    row.update(status=status)
    if status == 'completed':
        row['receipt'] = {'status': 'confirmed', 'idempotency_key': row['idempotency_key'],
                          'adapter': action, 'receipt_id': 'msg-1'}
    return row


def _states():
    rows = [_row('<a@example.com>', message='【知悉】个人:AcmeCorp 账单已出'),
            _row('<b@example.com>', message='【紧急】个人:AcmeCorp 付款失败'),
            _row('<c@example.com>', message='【待办】个人:周五前提交表格'),
            _row('<d@example.com>', status='completed', message='【紧急】个人:已送达的一条'),
            _row('<e@example.com>', action='topic_label', label='Finance')]
    state = em_actions.load_state({'actions': {r['idempotency_key']: r for r in rows}}, ACCOUNT)
    summary = {'summary_runs': {'run1': {'steps': [
        {'key': 'run1:alert', 'adapter': 'alert', 'status': 'uncertain',
         'payload': {'message': '每日汇总:2 项待办'}, 'receipt': None},
        {'key': 'run1:summary_mark_done', 'adapter': 'summary_mark_done', 'status': 'pending',
         'payload': {'item_id': 'x'}, 'receipt': None}]}}}
    return {'acct': state}, summary


HEADERS = {'<a@example.com>': {'date': 'Mon, 05 Oct 2026 09:00:00 -0400', 'from': 'Acme Billing <billing@example-employer.com>', 'subject': 'Your statement'},
           '<b@example.com>': {'date': 'Tue, 06 Oct 2026 10:00:00 -0400', 'from': 'Acme <pay@example-employer.com>', 'subject': 'Payment declined, code AB12CD34'},
           '<c@example.com>': {'date': 'Wed, 07 Oct 2026 11:00:00 -0400', 'from': 'HR <hr@example-employer.com>', 'subject': 'Form due Friday'}}


def lookup(account, mids):
    assert account == ACCOUNT
    return {m: HEADERS[m] for m in mids if m in HEADERS}


def confirmed_send(sent):
    def send(text, key, journal):
        sent.append((text, key, copy.deepcopy(journal)))
        return {'status': 'confirmed', 'idempotency_key': key, 'adapter': 'alert', 'receipt_id': '111,222'}
    return send


def test_one_message_most_important_first_then_every_member_marked_with_a_catchup_receipt():
    states, summary = _states()
    sent = []
    report, states2, summary2, journal = cu.run_alerts(states, summary, None, confirmed_send(sent), lookup)
    assert report['status'] == 'completed' and report['members'] == 4 and report['marked'] == 4
    assert len(sent) == 1, 'all undelivered alerts go out as ONE catch-up'
    text, key, journal_at_send = sent[0]
    # the intent was journaled before the effect
    assert journal_at_send['catchups'][key]['status'] == 'uncertain'
    order = [text.index(s) for s in ('付款失败', '周五前提交表格', '每日汇总:2 项待办', '账单已出')]
    assert order == sorted(order), 'URGENT, ACTION, the summary, then FYI'
    assert '10-06' in text and 'example-employer.com' in text and 'Payment declined' in text
    assert 'AB12CD34' not in text, 'subjects pass the normal alert redaction'
    assert '@' not in text, 'no addresses in the push'
    rows = states2['acct']['actions'].values()
    alerts = [r for r in rows if r['action'] == 'alert']
    assert all(r['status'] == 'completed' for r in alerts)
    marked = [r for r in alerts if r['receipt'].get('delivery') == 'catch_up']
    assert len(marked) == 3 and all(r['receipt']['catchup_key'] == key and
                                    r['receipt']['receipt_id'] == key + '/111,222' for r in marked)
    step = summary2['summary_runs']['run1']['steps'][0]
    assert step['status'] == 'completed' and step['receipt']['delivery'] == 'catch_up'
    assert summary2['summary_runs']['run1']['steps'][1]['status'] == 'pending', 'later steps run normally'
    # the marked ledger is still a valid ledger
    em_actions.load_state(states2['acct'], ACCOUNT)
    topic = [r for r in rows if r['action'] == 'topic_label'][0]
    assert topic['status'] == 'uncertain', 'catch-up touches alerts only'
    assert journal['catchups'][key]['status'] == 'completed'


@pytest.mark.parametrize('disposition', ['uncertain', 'not_applied', 'raise_none'])
def test_nothing_is_marked_unless_the_relay_confirms(disposition):
    states, summary = _states()

    def send(text, key, journal):
        if disposition == 'raise_none':
            return None
        receipt = {'status': disposition, 'idempotency_key': key, 'adapter': 'alert'}
        if disposition == 'not_applied':
            receipt['evidence'] = 'nothing sent'
        return receipt
    report, states2, summary2, journal = cu.run_alerts(states, summary, None, send, lookup)
    assert report['status'] == 'not_delivered'
    assert states2 == states and summary2 == summary
    if disposition == 'not_applied':
        assert journal['catchups'] == {}
    else:
        # an unproven send blocks a second catch-up rather than risk a duplicate
        with pytest.raises(RuntimeError, match='interrupted'):
            cu.run_alerts(states2, summary2, journal, confirmed_send([]), lookup)


def test_a_rerun_after_a_lost_save_marks_again_and_never_resends():
    states, summary = _states()
    sent = []
    _, _, _, journal = cu.run_alerts(states, summary, None, confirmed_send(sent), lookup)
    # A concurrent writer put the unmarked copy back; the rerun only re-marks.
    report, states2, summary2, _ = cu.run_alerts(states, summary, journal, confirmed_send(sent), lookup)
    assert len(sent) == 1
    assert report['status'] == 'nothing_pending' and report['remarked'] == 4
    assert all(r['status'] == 'completed' for r in states2['acct']['actions'].values() if r['action'] == 'alert')


def test_header_lookup_failure_still_delivers_with_time_unknown():
    states, summary = _states()
    sent = []

    def broken(account, mids):
        raise OSError('synthetic')
    report, *_ = cu.run_alerts(states, summary, None, confirmed_send(sent), broken)
    assert report['status'] == 'completed' and '时间未知' in sent[0][0]


def test_dry_run_sends_nothing():
    states, summary = _states()
    sent = []
    report, states2, _, journal = cu.run_alerts(states, summary, None, confirmed_send(sent), lookup, dry=True)
    assert report['status'] == 'planned' and report['members'] == 4 and not sent
    assert states2 == states and journal == {'catchups': {}}


def test_requeue_marks_only_uncertain_rows_of_that_action_not_applied():
    states, _ = _states()
    updated, count = cu.requeue(states['acct'], 'topic_label', 'helper rejected the keyed call')
    assert count == 1
    row = [r for r in updated['actions'].values() if r['action'] == 'topic_label'][0]
    assert row['status'] == 'failed'
    assert em_actions.receipt_status(row['receipt'], row['idempotency_key'], 'topic_label') == 'not_applied'
    assert all(r['status'] == states['acct']['actions'][k]['status']
               for k, r in updated['actions'].items() if r['action'] != 'topic_label')
    em_actions.load_state(updated, ACCOUNT)


@pytest.mark.parametrize('action', ['alert', 'pool'])
def test_requeue_refuses_alerts_and_pool(action):
    states, _ = _states()
    with pytest.raises(ValueError):
        cu.requeue(states['acct'], action, 'evidence')


def test_requeue_needs_evidence():
    states, _ = _states()
    with pytest.raises(ValueError):
        cu.requeue(states['acct'], 'topic_label', '  ')


def test_pending_rows_are_left_to_the_tick_and_later_or_undated_rows_are_not_caught_up():
    import datetime
    states, summary = _states()
    actions = states['acct']['actions']
    for row in (_row('<p@example.com>', status='pending', message='【紧急】个人:正在发送的一条'),
                _row('<late@example.com>', message='【紧急】个人:修复之后的一条'),
                _row('<nodate@example.com>', message='【紧急】个人:无日期的一条')):
        actions[row['idempotency_key']] = row
    headers = dict(HEADERS, **{'<late@example.com>': {'date': 'Fri, 09 Oct 2026 23:00:00 -0400',
                                                      'from': 'x <x@example.com>', 'subject': 'late'}})
    sent = []
    cutoff = datetime.datetime(2026, 10, 9, 20, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=-4)))
    report, states2, _, _ = cu.run_alerts(states, summary, None, confirmed_send(sent),
                                          lambda a, mids: {m: headers[m] for m in mids if m in headers},
                                          before=cutoff)
    assert report['members'] == 4
    text = sent[0][0]
    for left in ('正在发送的一条', '修复之后的一条', '无日期的一条'):
        assert left not in text
    by_mid = {r['message_id']: r for r in states2['acct']['actions'].values()}
    assert by_mid['<p@example.com>']['status'] == 'pending'
    assert by_mid['<late@example.com>']['status'] == 'uncertain'
    assert by_mid['<nodate@example.com>']['status'] == 'uncertain'


def test_cutoff_must_carry_an_offset():
    with pytest.raises(ValueError):
        cu._cutoff('2026-10-09T20:00:00')
    assert cu._cutoff('2026-10-09T20:00:00-04:00').utcoffset() is not None
