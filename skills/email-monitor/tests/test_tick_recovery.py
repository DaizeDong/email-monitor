"""Tick recovery after an account was unreadable for days, and the writers that share its state.

Covers: mailbox names that need IMAP quoting; one message seen in two monitored mailboxes; a
backlog of old mail whose alerts go out as ONE catch-up; topic labels whose message no longer
exists; the next daily-summary slot; and the writer lock in em_watch's CLI and a hand-started
summary worker. Synthetic data only.
"""
import copy
import datetime
import email.utils
import imaplib
import json
import os
from pathlib import Path
import sys

import pytest

from test_durable_dispatch import FIX, ack, harness, _configured_registry  # noqa: F401  (fixture)
from test_summary_reconcile import _state as summary_state, worker  # noqa: F401  (fixture)
from test_writer_lock import lock_state

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import em_actions
import em_catchup
import em_runtime
import em_summary
import em_tick as tick
import em_watch

REAL_LOAD, REAL_SAVE = em_watch.load_state, em_watch.save_state
ACCOUNT = FIX['account']['user']
ALL_MAIL = '[Gmail]/All Mail'
RAW = (b'From: sender@example.com\r\nSubject: Synthetic update\r\n'
       b'Message-ID: <observation@example.com>\r\n\r\nSynthetic body')


# ---------- mailbox names reach IMAP as quoted strings ----------

class StrictServer:
    """Answers like Gmail: an unquoted mailbox name with a space is several tokens -> BAD."""

    def __init__(self, uidnext=1005):
        self.selected, self.statused, self.ranges = [], [], []
        self.uidnext = uidnext

    @staticmethod
    def _parse(name):
        if not (name.startswith('"') and name.endswith('"')) and (' ' in name or '"' in name):
            raise imaplib.IMAP4.error("EXAMINE command error: BAD [b'Could not parse command']")

    def login(self, *args):
        return 'OK', []

    def select(self, mailbox, readonly):
        assert readonly
        self._parse(mailbox)
        self.selected.append(mailbox)
        return 'OK', []

    def status(self, mailbox, items):
        self._parse(mailbox)
        self.statused.append(mailbox)
        return 'OK', [b'X (UIDVALIDITY 3 UIDNEXT %d)' % self.uidnext]

    def uid(self, verb, span, attributes):
        self.ranges.append(span)
        return 'OK', [(b'1 (UID 1004 X-GM-MSGID 1004 X-GM-THRID 1004)', RAW)]

    def logout(self):
        return 'BYE', []


@pytest.mark.parametrize('folder', [ALL_MAIL, 'Receipts 2026', 'INBOX'])
def test_every_mailbox_name_reaches_select_and_status_quoted(monkeypatch, folder):
    server = StrictServer()
    monkeypatch.setattr(em_watch.imaplib, 'IMAP4_SSL', lambda *a: server)
    records, cursor = em_watch.run_once(ACCOUNT, folder, {'uidvalidity': 3, 'last_uid': 1000}, 200,
                                        app_pw='synthetic-auth')
    assert server.selected == server.statused == ['"%s"' % folder]
    assert [r['uid'] for r in records] == [1004] and cursor == {'uidvalidity': 3, 'last_uid': 1004}


def test_quotes_and_backslashes_inside_a_name_are_escaped():
    assert em_watch.quote_mailbox('a "b" \\ c') == '"a \\"b\\" \\\\ c"'
    assert em_watch.quote_mailbox('"INBOX"') == '"INBOX"'


LOCALIZED_ALL = '[Gmail]/&YkBnCZCuTvY-'   # a non-English account's All Mail, in modified UTF-7


class LocalizedServer(StrictServer):
    """A Gmail account whose display language is not English: "[Gmail]/All Mail" does not exist."""

    def list(self, *args):
        return 'OK', [b'(\\HasNoChildren) "/" "INBOX"',
                      b'(\\HasNoChildren \\Junk) "/" "[Gmail]/&V4NXPpCuTvY-"',
                      b'(\\All \\HasNoChildren) "/" "%s"' % LOCALIZED_ALL.encode()]

    def select(self, mailbox, readonly):
        if mailbox not in ('"INBOX"', '"%s"' % LOCALIZED_ALL):
            return 'NO', [b'Failure']
        return super().select(mailbox, readonly)


def test_the_all_mail_special_use_resolves_to_the_localized_name(monkeypatch):
    server = LocalizedServer()
    monkeypatch.setattr(em_watch.imaplib, 'IMAP4_SSL', lambda *a: server)
    records, cursor = em_watch.run_once(ACCOUNT, '\\All', {'uidvalidity': 3, 'last_uid': 1000}, 200,
                                        app_pw='synthetic-auth')
    assert server.selected == server.statused == ['"%s"' % LOCALIZED_ALL]
    assert [r['uid'] for r in records] == [1004]


def test_a_missing_special_use_mailbox_fails_by_name(monkeypatch):
    server = LocalizedServer()
    monkeypatch.setattr(server, 'list', lambda *a: ('OK', [b'(\\HasNoChildren) "/" "INBOX"']))
    monkeypatch.setattr(em_watch.imaplib, 'IMAP4_SSL', lambda *a: server)
    with pytest.raises(RuntimeError, match=r'\\All'):
        em_watch.run_once(ACCOUNT, '\\All', {'uidvalidity': 3, 'last_uid': 1000}, 200, app_pw='synthetic-auth')
    assert not server.selected


@pytest.mark.parametrize('uidnext,max_batch,caught_up',[(1005, 200, True), (1500, 200, False)])
def test_fetch_reports_whether_it_reached_the_mailbox_tip(monkeypatch, uidnext, max_batch, caught_up):
    server = StrictServer(uidnext)
    monkeypatch.setattr(em_watch.imaplib, 'IMAP4_SSL', lambda *a: server)
    info = {}
    em_watch.run_once(ACCOUNT, 'INBOX', {'uidvalidity': 3, 'last_uid': 1000}, max_batch,
                      app_pw='synthetic-auth', info=info)
    assert info == {'caught_up': caught_up}


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


def test_all_mail_is_read_after_inbox_and_a_later_copy_is_still_recognised(harness, monkeypatch):
    message = _message(2, 0)
    calls = _mailboxes(monkeypatch, {ALL_MAIL: [], 'INBOX': [message]})
    _verdicts(monkeypatch)
    sent = _relay(monkeypatch)
    assert _run(harness, _account([ALL_MAIL, 'INBOX']))['status'] == 'completed'
    assert calls == ['INBOX', ALL_MAIL]
    # Next tick: the same message only now shows up in All Mail (it was filed after INBOX was read).
    _mailboxes(monkeypatch, {ALL_MAIL: [message], 'INBOX': []})
    result = _run(harness, _account([ALL_MAIL, 'INBOX']))
    assert result['new'] == 0 and len(sent) == 1


def test_a_message_only_in_all_mail_is_still_seen(harness, monkeypatch):
    filtered = _message(3, 0, subject='Your verification code')
    _mailboxes(monkeypatch, {'INBOX': [], ALL_MAIL: [filtered]})
    _verdicts(monkeypatch)
    sent = _relay(monkeypatch)
    assert _run(harness, _account(['INBOX', ALL_MAIL]))['new'] == 1
    assert len(sent) == 1


# ---------- a backlog of old mail: one catch-up, delivered only on confirmation ----------

@pytest.fixture
def real_files(harness, monkeypatch):
    monkeypatch.setattr(tick.em_watch, 'load_state', REAL_LOAD)
    monkeypatch.setattr(tick.em_watch, 'save_state', REAL_SAVE)
    state_dir = harness.companion / 'state'
    return state_dir / 'user1.state.json', state_dir / em_catchup.JOURNAL


def test_old_mail_alerts_go_out_as_one_catch_up_and_fresh_mail_alerts_normally(harness, monkeypatch, real_files):
    state_path, journal_path = real_files
    old = [_message(n, 10 - n) for n in range(4)]
    fresh = _message(9, 0)
    _mailboxes(monkeypatch, {'INBOX': old + [fresh]})
    _verdicts(monkeypatch, {old[0]['subject']: 'URGENT', old[3]['subject']: 'FYI'})
    sent = _relay(monkeypatch)
    result = _run(harness, _account())
    assert result['status'] == 'completed'
    assert len(sent) == 2, 'old mail was alerted one by one'
    texts = [text for text, key in sent]
    catchup = next(text for text, key in sent if key.startswith('catchup:'))
    assert catchup.startswith('补发') and catchup.count('【') == 3, 'the catch-up carries the three old alerts'
    assert sum(t.startswith('【') for t in texts) == 1, 'the fresh alert is sent on its own'
    assert result['backlog'] == {'status': 'completed', 'members': 3, 'levels': {'URGENT': 1, 'ACTION': 2}}
    state = json.loads(state_path.read_text(encoding='utf-8'))
    rows = _alerts(state)
    assert all(row['status'] == 'completed' for row in rows)
    caught = [row for row in rows if row['receipt'].get('delivery') == 'catch_up']
    assert len(caught) == 3 and all(row['payload']['backlog'] for row in caught)
    journal = json.loads(journal_path.read_text(encoding='utf-8'))
    (entry,) = journal['catchups'].values()
    assert entry['status'] == 'completed' and entry['kind'] == 'backlog' and len(entry['members']) == 3
    # The old messages were still classified and recorded like any other.
    assert len(state['observed_messages']) == 5
    # A later tick sends nothing more.
    _mailboxes(monkeypatch, {'INBOX': []})
    assert _run(harness, _account())['status'] == 'completed' and len(sent) == 2


def test_backlog_waits_until_every_mailbox_is_read_to_its_tip(harness, monkeypatch, real_files):
    _mailboxes(monkeypatch, {'INBOX': [_message(1, 5)]}, caught_up={'INBOX': False})
    _verdicts(monkeypatch)
    sent = _relay(monkeypatch)
    result = _run(harness, _account())
    assert result['status'] == 'incomplete' and not sent, 'a backlog was sent before the account caught up'
    _mailboxes(monkeypatch, {'INBOX': [_message(2, 4)]})
    result = _run(harness, _account())
    assert result['status'] == 'completed' and len(sent) == 1
    assert result['backlog']['members'] == 2


def test_a_long_backlog_flushes_held_alerts_after_six_hours_without_waiting_for_the_tip(
        harness, monkeypatch, real_files):
    state_path, journal_path = real_files
    _mailboxes(monkeypatch, {'INBOX': [_message(1, 5), _message(2, 4)]}, caught_up={'INBOX': False})
    _verdicts(monkeypatch)
    sent = _relay(monkeypatch)
    assert _run(harness, _account())['status'] == 'incomplete' and not sent
    # The account is still far behind, but the held alerts have now waited past the flush period.
    state = json.loads(state_path.read_text(encoding='utf-8'))
    earlier = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=6, minutes=1)).isoformat()
    for row in _alerts(state):
        row['payload']['held_at'] = earlier
    state_path.write_text(json.dumps(state), encoding='utf-8')
    _mailboxes(monkeypatch, {'INBOX': [_message(3, 3)]}, caught_up={'INBOX': False})
    result = _run(harness, _account())
    assert len(sent) == 1 and result['backlog']['members'] == 3, 'held alerts waited for the tip forever'
    # A newly held alert starts a new period and waits again.
    _mailboxes(monkeypatch, {'INBOX': [_message(4, 2)]}, caught_up={'INBOX': False})
    assert _run(harness, _account())['status'] == 'incomplete' and len(sent) == 1


def test_backlog_refused_by_the_relay_is_retried_as_one_message(harness, monkeypatch, real_files):
    state_path, journal_path = real_files
    _mailboxes(monkeypatch, {'INBOX': [_message(1, 5), _message(2, 6)]})
    _verdicts(monkeypatch)
    sent = _relay(monkeypatch, 'not_applied')
    assert _run(harness, _account())['status'] == 'incomplete'
    rows = _alerts(json.loads(state_path.read_text(encoding='utf-8')))
    assert {row['status'] for row in rows} == {'failed'}
    assert json.loads(journal_path.read_text(encoding='utf-8'))['catchups'] == {}
    sent2 = _relay(monkeypatch)
    _mailboxes(monkeypatch, {'INBOX': []})
    assert _run(harness, _account())['status'] == 'completed'
    assert len(sent) == 1 and len(sent2) == 1


def test_backlog_with_an_unknown_outcome_is_never_sent_again(harness, monkeypatch, real_files):
    state_path, journal_path = real_files
    _mailboxes(monkeypatch, {'INBOX': [_message(1, 5), _message(2, 6)]})
    _verdicts(monkeypatch)
    sent = _relay(monkeypatch, 'unknown')
    assert _run(harness, _account())['status'] == 'incomplete'
    _mailboxes(monkeypatch, {'INBOX': []})
    assert _run(harness, _account())['status'] == 'incomplete'
    assert len(sent) == 1
    rows = _alerts(json.loads(state_path.read_text(encoding='utf-8')))
    assert {row['status'] for row in rows} == {'uncertain'}
    (entry,) = json.loads(journal_path.read_text(encoding='utf-8'))['catchups'].values()
    assert entry['status'] == 'uncertain'


def test_dry_run_plans_the_backlog_without_sending(harness, monkeypatch):
    _mailboxes(monkeypatch, {'INBOX': [_message(1, 5)]})
    _verdicts(monkeypatch)
    sent = _relay(monkeypatch)
    result = _run(harness, _account(), dry=True)
    assert result['status'] == 'planned' and not sent
    (alert,) = [row for row in result['planned_actions'] if row['action'] == 'alert']
    assert alert['payload']['backlog'] is True and alert['payload']['origin']['subject']


# ---------- a topic label whose message no longer exists ----------

def _gone_row(status='failed', action='topic_label'):
    row = em_actions.new_action(ACCOUNT, 'INBOX', 7, {'message_id': '<gone@example.com>'}, action,
                                {'label': 'Finance'})
    row.update(status=status, receipt=_gone_receipt(row['idempotency_key'], action))
    return row


def _gone_receipt(key, action='topic_label'):
    return {'idempotency_key': key, 'adapter': action, 'status': 'not_applied', 'matched': 0,
            'applied': False, 'evidence': 'query matched no message; nothing was changed'}


def _label_tool(monkeypatch, answers):
    calls = []

    def label(user, message_id, label, dry, app_pw=None, idempotency_key=None, python=None):
        calls.append(idempotency_key)
        answer = answers[min(len(calls), len(answers)) - 1]
        if answer == 'gone':
            return _gone_receipt(idempotency_key)
        if answer == 'refused':
            return {'idempotency_key': idempotency_key, 'adapter': 'topic_label', 'status': 'not_applied',
                    'evidence': 'mailbox select refused; nothing was changed'}
        return {'idempotency_key': idempotency_key, 'adapter': 'topic_label', 'status': 'confirmed',
                'receipt_id': 'labelled', 'matched': 1, 'applied': True}
    monkeypatch.setattr(tick, '_label_add', label)
    return calls


def test_a_label_for_a_message_that_is_gone_stops_after_three_answers(harness, monkeypatch):
    row = _gone_row()
    harness.stored['value'] = {'cursors': {}, 'actions': {row['idempotency_key']: row}}
    harness.fetched['records'] = []
    calls = _label_tool(monkeypatch, ['gone'])
    statuses = [_run(harness, _account())['status'] for _ in range(3)]
    assert statuses == ['incomplete', 'incomplete', 'completed'], 'a vanished message keeps the tick incomplete'
    stored = harness.stored['value']['actions'][row['idempotency_key']]
    assert stored['status'] == 'message_gone' and stored['gone_checks'] == em_actions.GONE_AFTER
    assert _run(harness, _account())['status'] == 'completed'
    assert len(calls) == 3, 'a message_gone row was dispatched again'


def test_another_answer_restarts_the_count(harness, monkeypatch):
    row = _gone_row()
    harness.stored['value'] = {'cursors': {}, 'actions': {row['idempotency_key']: row}}
    harness.fetched['records'] = []
    _label_tool(monkeypatch, ['gone', 'gone', 'refused', 'gone', 'gone'])
    statuses = [_run(harness, _account())['status'] for _ in range(5)]
    assert statuses == ['incomplete'] * 5
    assert harness.stored['value']['actions'][row['idempotency_key']]['gone_checks'] == 2


def test_message_gone_needs_its_proof_when_state_is_loaded():
    row = _gone_row(status='message_gone')
    row['gone_checks'] = em_actions.GONE_AFTER
    assert em_actions.load_state({'actions': {row['idempotency_key']: row}}, ACCOUNT)
    for broken in ({'gone_checks': 1}, {'receipt': None}, {'action': 'alert'}):
        bad = {**row, **broken}
        if 'action' in broken:
            bad['receipt'] = _gone_receipt(row['idempotency_key'], 'alert')
        with pytest.raises(ValueError):
            em_actions.load_state({'actions': {row['idempotency_key']: bad}}, ACCOUNT)


# ---------- the next daily-summary slot ----------

NY = em_summary.NY


@pytest.mark.parametrize('now,expected', [
    (datetime.datetime(2026, 10, 10, 2, 46, tzinfo=NY), '2026-10-10T12:00:00Z'),   # today's slot still ahead
    (datetime.datetime(2026, 10, 10, 8, 0, tzinfo=NY), '2026-10-11T12:00:00Z'),    # the slot itself has passed
    (datetime.datetime(2026, 10, 10, 9, 30, tzinfo=NY), '2026-10-11T12:00:00Z'),
    (datetime.datetime(2026, 11, 1, 1, 30, tzinfo=NY), '2026-11-01T13:00:00Z'),    # DST ends that night
    (datetime.datetime(2026, 3, 7, 23, 0, tzinfo=NY), '2026-03-08T12:00:00Z'),     # DST starts overnight
])
def test_the_next_summary_slot_is_the_next_one_still_ahead(now, expected):
    assert em_summary.next_summary_utc('08:00', now=now) == expected


# ---------- writers outside the tick take the lock ----------

def _summary_state_dir(harness):
    return harness.companion / 'data' / 'state'


def test_a_hand_started_summary_is_refused_while_another_writer_holds_the_lock(worker, harness, capsys):
    pool, path, steps = worker
    path.write_text(json.dumps(summary_state()), encoding='utf-8')
    before = path.read_text(encoding='utf-8')
    with em_runtime.writer_lock(_summary_state_dir(harness), 'catchup'):
        assert em_summary.main() == 3
    assert path.read_text(encoding='utf-8') == before and not pool['calls']
    assert json.loads(capsys.readouterr().out.splitlines()[-1])['status'] == 'refused'


def test_a_summary_started_by_the_tick_works_under_the_ticks_lock(worker, harness, monkeypatch):
    pool, path, steps = worker
    path.write_text(json.dumps(summary_state()), encoding='utf-8')
    monkeypatch.setattr(em_summary.os, 'getppid', lambda: os.getpid())
    with em_runtime.writer_lock(_summary_state_dir(harness), 'tick'):
        assert em_summary.main() == 0
    assert steps()['summary_mark_done']['status'] == 'completed'


def test_a_tick_that_is_not_the_parent_does_not_cover_a_summary(worker, harness, monkeypatch):
    pool, path, steps = worker
    path.write_text(json.dumps(summary_state()), encoding='utf-8')
    monkeypatch.setattr(em_summary.os, 'getppid', lambda: os.getpid() + 1)
    with em_runtime.writer_lock(_summary_state_dir(harness), 'tick'):
        assert em_summary.main() == 3
    assert not pool['calls']


def test_a_hand_started_summary_holds_the_lock_while_it_works(worker, harness, monkeypatch):
    pool, path, steps = worker
    path.write_text(json.dumps(summary_state()), encoding='utf-8')
    seen = []
    original = em_summary.em_pool.mark_done

    def mark_done(*a, **k):
        seen.append(lock_state(_summary_state_dir(harness)))
        return original(*a, **k)
    monkeypatch.setattr(em_summary.em_pool, 'mark_done', mark_done)
    pool['state'] = 'pending'
    assert em_summary.main() == 0
    assert seen == ['busy']
    assert lock_state(_summary_state_dir(harness)) == 'free'


def _watch_argv(monkeypatch, state_path):
    monkeypatch.setattr(sys, 'argv', ['em_watch.py', '--user', ACCOUNT, '--state', str(state_path)])


def test_the_watch_cli_is_refused_while_another_writer_holds_the_lock(harness, monkeypatch):
    state_dir = harness.companion / 'state'
    _watch_argv(monkeypatch, state_dir / 'user1.state.json')
    monkeypatch.setattr(em_watch, 'run_once', lambda *a, **k: pytest.fail('mail was read under a held lock'))
    with em_runtime.writer_lock(state_dir, 'tick'):
        assert em_watch.main() == 3
    assert not harness.saves


def test_the_watch_cli_holds_the_lock_while_it_writes(harness, monkeypatch, capsys):
    state_dir = harness.companion / 'state'
    _watch_argv(monkeypatch, state_dir / 'user1.state.json')
    seen = []

    def run_once(*a, **k):
        seen.append(lock_state(state_dir))
        return [], {'uidvalidity': 7, 'last_uid': 11}
    monkeypatch.setattr(em_watch, 'run_once', run_once)
    assert em_watch.main() == 0
    assert seen == ['busy'] and harness.saves
    assert lock_state(state_dir) == 'free'
