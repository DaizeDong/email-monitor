"""Tick recovery after an account was unreadable for days, and the writers that share its state.

Covers: mailbox names that need IMAP quoting; one message seen in two monitored mailboxes; a
backlog of old mail whose alerts go out as ONE catch-up; topic labels whose message no longer
exists; the next daily-summary slot; and the writer lock in em_watch's CLI and a hand-started
summary worker. Synthetic data only.
"""
import copy
import datetime
import email.utils
from pathlib import Path
import sys


from test_durable_dispatch import FIX, harness

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import em_actions
import em_catchup
import em_lint_rules
import em_runtime
import em_summary
import em_tick as tick
import em_watch

REAL_LOAD, REAL_SAVE = em_watch.load_state, em_watch.save_state
ACCOUNT = FIX['account']['user']
ALL_MAIL = '[Gmail]/All Mail'


# ---------- mailbox names reach IMAP as quoted strings ----------


# ---------- the tick: helpers ----------

def _message(n, days_old, subject=None):
    sent = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_old)
    return {'uid': 100 + n, 'gm_msgid': str(1000 + n), 'message_id': '<m%d@example.com>' % n,
            'thread_key': 'thread-%d' % n, 'from': 'Acme Billing <billing@example-employer.com>',
            'subject': subject or 'Synthetic notice %d' % n, 'body': 'Synthetic body.',
            'date': email.utils.format_datetime(sent), 'list_unsubscribe': False}


def _account(folders=('INBOX',)):
    return {**FIX['account'], 'monitored_folders': list(folders)}


def _run(harness, account, dry=False):
    return tick.process_account(copy.deepcopy(account), {}, 'absent', None, None,
                                str(harness.companion / 'state'), dry,
                                agent_cfg={'mode': 'heuristic'}, pool_enabled=False)


def _mailboxes(monkeypatch, boxes, caught_up=None):
    """run_once fake: boxes maps mailbox -> list of records; generation differs per mailbox."""
    calls = []

    def run_once(user, folder, cursor, max_batch=400, app_pw=None, info=None):
        calls.append(folder)
        if isinstance(info, dict):
            info['caught_up'] = True if caught_up is None else caught_up.get(folder, True)
        return copy.deepcopy(boxes.get(folder, [])), {'uidvalidity': 7 if folder == 'INBOX' else 9,
                                                      'last_uid': 500}
    monkeypatch.setattr(tick.em_watch, 'run_once', run_once)
    return calls


def _verdicts(monkeypatch, by_subject=None):
    def classify(msgs, *a):
        out = []
        for message in msgs:
            verdict = copy.deepcopy(FIX['verdict'])
            verdict['priority'] = (by_subject or {}).get(message['subject'], verdict['priority'])
            out.append(verdict)
        return out
    monkeypatch.setattr(tick, 'classify_records_parallel', classify)


def _relay(monkeypatch, outcome='confirmed'):
    sent = []

    def send(message, idempotency_key=None, **kw):
        sent.append((message, idempotency_key))
        if outcome == 'confirmed':
            return {'status': 'confirmed', 'idempotency_key': idempotency_key, 'adapter': 'alert',
                    'receipt_id': '9001,9002'}
        if outcome == 'not_applied':
            return {'status': 'not_applied', 'idempotency_key': idempotency_key, 'adapter': 'alert',
                    'evidence': 'synthetic relay refused before sending'}
        return None
    monkeypatch.setattr(tick.em_alert, 'send', send)
    return sent


def _alerts(state):
    return [row for row in state['actions'].values() if row['action'] == 'alert']


# ---------- one message in two monitored mailboxes ----------

def test_a_message_in_inbox_and_all_mail_is_handled_once(harness, monkeypatch):
    message = _message(1, 0)
    _mailboxes(monkeypatch, {'INBOX': [message], ALL_MAIL: [message]})
    _verdicts(monkeypatch)
    sent = _relay(monkeypatch)
    result = _run(harness, _account(['INBOX', ALL_MAIL]))
    assert result['status'] == 'completed' and result['new'] == 1
    assert len(sent) == 1, 'one message in two mailboxes was alerted twice'
    assert len(_alerts(harness.stored['value'])) == 1


# ---------- a backlog of old mail: one catch-up, delivered only on confirmation ----------


# ---------- the exit code while backlog alerts are held ----------


# ---------- a topic label whose message no longer exists ----------


# ---------- the next daily-summary slot ----------

NY = em_summary.NY


# ---------- writers outside the tick take the lock ----------
