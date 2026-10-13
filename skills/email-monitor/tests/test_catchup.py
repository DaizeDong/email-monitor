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


@pytest.mark.parametrize('disposition', ['uncertain'])
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
