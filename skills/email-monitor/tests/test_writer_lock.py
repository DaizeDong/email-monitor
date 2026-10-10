"""One writer per state directory: em_catchup against a running tick, and the tick's own lock.

The lock is exercised through its documented protocol (byte 0 of <state_dir>/.writer.lock, held
through another open handle, which the OS treats as another holder: Windows byte-range locks belong
to the handle, flock to the open file description), so a writer that ignores it is caught.
The offline suite forbids child processes, so the other holder lives in this process.
Synthetic data only.
"""
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import em_actions
import em_catchup as cu
import em_pool
import em_runtime
import em_tick
import em_watch
from private_storage_helpers import make_repository, write_visibility

ACCOUNT = 'user1@example.com'
FIX = json.loads((Path(__file__).parent / 'reliability.json').read_text(encoding='utf-8'))
CUTOFF = '2026-10-09T20:00:00-04:00'
HEADERS = {'<a@example.com>': {'date': 'Tue, 06 Oct 2026 10:00:00 -0400', 'from': 'Acme <pay@example-employer.com>',
                               'subject': 'Payment declined'},
           '<b@example.com>': {'date': 'Wed, 07 Oct 2026 11:00:00 -0400', 'from': 'HR <hr@example-employer.com>',
                               'subject': 'Form due Friday'}}
SUMMARY_DUE = '2026-10-06T12:00:00Z'


def _lock(stream, unlock=False):
    stream.seek(0)
    if sys.platform == 'win32':
        import msvcrt
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream, fcntl.LOCK_UN if unlock else fcntl.LOCK_EX | fcntl.LOCK_NB)


@contextmanager
def another_writer(state_dir):
    """Another holder (a running tick) has the state directory's writer lock."""
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    with open(Path(state_dir) / '.writer.lock', 'a+b') as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        _lock(stream)
        try:
            yield
        finally:
            _lock(stream, unlock=True)


def lock_state(state_dir):
    path = Path(state_dir) / '.writer.lock'
    if not path.exists():
        return 'free'
    with open(path, 'a+b') as stream:
        try:
            _lock(stream)
        except OSError:
            return 'busy'
        _lock(stream, unlock=True)
        return 'free'


def _row(mid, action='alert', status='uncertain', message='', label=''):
    payload = {'message': message} if action == 'alert' else {'label': label}
    row = em_actions.new_action(ACCOUNT, 'INBOX', 7, {'message_id': mid}, action, payload)
    row.update(status=status)
    return row


@pytest.fixture
def companion(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    if os.name == 'nt':
        import ctypes

        def profile_folder(window, folder, token, flags, buffer):
            buffer.value = str(tmp_path)
            return 0
        monkeypatch.setattr(ctypes.windll.shell32, 'SHGetFolderPathW', profile_folder)
    root = make_repository(tmp_path / 'companion')
    write_visibility(tmp_path)
    cfg = {'schema_version': 1, 'accounts': [{'slug': 'user1', 'user': ACCOUNT}],
           'storage': {'state_dir': 'data/state', 'db': 'data/pool.db', 'log': 'data/log.txt'}}
    registry = root / 'registry.json'
    registry.write_text(json.dumps(cfg), encoding='utf-8')
    state_dir = root / 'data' / 'state'
    state_dir.mkdir(parents=True)
    rows = [_row('<a@example.com>', message='【紧急】个人:AcmeCorp 付款失败'),
            _row('<b@example.com>', message='【待办】个人:周五前提交表格'),
            _row('<t@example.com>', action='topic_label', label='Finance')]
    account = {'cursors': {}, 'actions': {r['idempotency_key']: r for r in rows}}
    summary = {'summary_runs': {'run1': {'steps': [
        {'key': 'run1:alert', 'adapter': 'alert', 'status': 'uncertain',
         'payload': {'message': '每日汇总:2 项待办'}, 'receipt': None},
        {'key': 'run1:summary_mark_done', 'adapter': 'summary_mark_done', 'status': 'pending',
         'payload': {'item_id': 'item-1'}, 'receipt': None}]}}}
    account_path, summary_path = state_dir / 'user1.state.json', state_dir / 'summary.state.json'
    account_path.write_text(json.dumps(account), encoding='utf-8')
    summary_path.write_text(json.dumps(summary), encoding='utf-8')
    sent = []

    def send(text, idempotency_key=None, **kw):
        sent.append(text)
        return {'status': 'confirmed', 'idempotency_key': idempotency_key, 'adapter': 'alert',
                'receipt_id': '111,222'}
    monkeypatch.setattr(cu.em_alert, 'send', send)
    monkeypatch.setattr(cu, 'imap_lookup', lambda user, password, mids: {m: HEADERS[m] for m in mids if m in HEADERS})
    monkeypatch.setattr(em_tick, 'resolve_app_pw', lambda *a: 'synthetic-password')
    monkeypatch.setattr(em_pool, 'list_items', lambda *a, **k: [{'id': 'item-1', 'due_at': SUMMARY_DUE}])
    keys = {r['message_id']: r['idempotency_key'] for r in rows}
    return SimpleNamespace(root=root, registry=str(registry), state_dir=state_dir, account_path=account_path,
                           summary_path=summary_path, sent=sent, keys=keys)


def snapshot(state_dir):
    return {p.name: p.read_bytes() for p in Path(state_dir).iterdir() if p.is_file() and p.name != '.writer.lock'}


@pytest.mark.parametrize('command', [['requeue', '--action', 'topic_label', '--evidence', 'helper rejected the call'],
                                     ['alerts', '--before', CUTOFF]])
def test_catchup_refuses_while_another_writer_holds_the_state_directory(companion, command, capsys):
    before = snapshot(companion.state_dir)
    with another_writer(companion.state_dir):
        code = cu.main(['--config', companion.registry, *command])
    assert code != 0, 'a catch-up must not run while a tick holds the state directory'
    assert not companion.sent, 'nothing is sent while another writer holds the state directory'
    assert snapshot(companion.state_dir) == before, 'no state file is written while another writer holds it'
    out = capsys.readouterr()
    assert json.loads(out.out.strip().splitlines()[-1])['status'] == 'refused'
    assert 'writer' in out.err
    # The same command runs once the other writer has exited.
    assert cu.main(['--config', companion.registry, *command]) == 0
    assert snapshot(companion.state_dir) != before


def test_catchup_marks_never_revert_rows_another_writer_advanced(companion):
    advanced = {}

    def send_while_a_tick_writes(text, idempotency_key=None, **kw):
        # A writer that ignores the lock (an older tick) checkpoints its own progress while the
        # catch-up is sending: it completes the label row and plans a new alert.
        state = json.loads(companion.account_path.read_text(encoding='utf-8'))
        label = companion.keys['<t@example.com>']
        state['actions'][label].update(status='completed', receipt={
            'status': 'confirmed', 'idempotency_key': label, 'adapter': 'topic_label', 'receipt_id': 'label-9',
            'matched': 1, 'applied': True})
        fresh = _row('<new@example.com>', status='pending', message='【紧急】个人:刚到的一条')
        state['actions'][fresh['idempotency_key']] = fresh
        advanced['new'] = fresh['idempotency_key']
        companion.account_path.write_text(json.dumps(state), encoding='utf-8')
        companion.sent.append(text)
        return {'status': 'confirmed', 'idempotency_key': idempotency_key, 'adapter': 'alert',
                'receipt_id': '111,222'}
    cu.em_alert.send = send_while_a_tick_writes  # restored by the fixture's monkeypatch
    assert cu.main(['--config', companion.registry, 'alerts', '--before', CUTOFF]) == 0
    state = json.loads(companion.account_path.read_text(encoding='utf-8'))
    label = state['actions'][companion.keys['<t@example.com>']]
    assert label['status'] == 'completed' and label['receipt']['receipt_id'] == 'label-9', \
        'the catch-up wrote back its stale copy over a row the other writer advanced'
    assert state['actions'].get(advanced['new'], {}).get('status') == 'pending', \
        'the catch-up dropped a row the other writer added'
    for mid in ('<a@example.com>', '<b@example.com>'):
        row = state['actions'][companion.keys[mid]]
        assert row['status'] == 'completed' and row['receipt']['delivery'] == 'catch_up'
    em_actions.load_state(state, ACCOUNT)
    summary = json.loads(companion.summary_path.read_text(encoding='utf-8'))
    assert summary['summary_runs']['run1']['steps'][0]['status'] == 'completed'
    assert len(companion.sent) == 1


def test_requeue_skips_a_row_another_writer_resolved_after_the_catchup_loaded_it(companion, monkeypatch):
    label = companion.keys['<t@example.com>']
    real_load = em_watch.load_state
    loads = []

    def load_then_tick_resolves(path):
        state = real_load(path)
        if Path(path) == companion.account_path:
            loads.append(path)
            if len(loads) == 1:  # right after the catch-up's first read, before any write
                disk = copy.deepcopy(state)
                disk['actions'][label].update(status='completed', receipt={
                    'status': 'confirmed', 'idempotency_key': label, 'adapter': 'topic_label',
                    'receipt_id': 'label-9', 'matched': 1, 'applied': True})
                companion.account_path.write_text(json.dumps(disk), encoding='utf-8')
        return state
    monkeypatch.setattr(em_watch, 'load_state', load_then_tick_resolves)
    assert cu.main(['--config', companion.registry, 'requeue', '--action', 'topic_label',
                    '--evidence', 'helper rejected the call']) == 0
    row = json.loads(companion.account_path.read_text(encoding='utf-8'))['actions'][label]
    assert row['status'] == 'completed' and row['receipt']['receipt_id'] == 'label-9', \
        'requeue overwrote a completed row with its stale copy'


def test_a_summary_in_the_catchup_shows_its_own_scheduled_time(companion):
    import datetime
    assert cu.main(['--config', companion.registry, 'alerts', '--before', CUTOFF]) == 0
    line = next(line for line in companion.sent[0].splitlines() if line.startswith('[每日汇总]'))
    expected = datetime.datetime.fromisoformat(SUMMARY_DUE.replace('Z', '+00:00')).astimezone()
    assert '时间未知' not in line and expected.strftime('%m-%d %H:%M') in line


def test_a_recorded_scheduled_time_needs_no_pool_lookup():
    import datetime
    summary = {'summary_runs': {'run1': {'scheduled_at': SUMMARY_DUE, 'steps': [
        {'key': 'run1:alert', 'adapter': 'alert', 'status': 'uncertain',
         'payload': {'message': '每日汇总'}, 'receipt': None}]}}}
    entries = cu.collect({}, summary)
    assert cu._when(entries[0]) == datetime.datetime(2026, 10, 6, 12, 0, tzinfo=datetime.timezone.utc).astimezone()


@pytest.fixture
def tick_main(tmp_path, monkeypatch):
    state_dir = tmp_path / 'state'
    cfg = {'schema_version': 1, 'accounts': [{'slug': 'user1', 'user': ACCOUNT}],
           'runtime': {'python': sys.executable}, 'classifier': {'mode': 'heuristic'}, 'draft': FIX['draft']}
    registry = tmp_path / 'registry.json'
    registry.write_text(json.dumps(cfg), encoding='utf-8')
    storage = {'state_dir': str(state_dir), 'db': str(tmp_path / 'pool.db'), 'log': str(tmp_path / 'log.txt')}
    monkeypatch.setattr(em_tick.em_runtime, 'storage_config', lambda *a, **k: (dict(storage), {}))
    monkeypatch.setattr(em_tick.em_runtime, 'prove_private', lambda *a, **k: {})
    monkeypatch.setattr(em_tick.em_runtime, 'probe_interpreter', lambda *a: (True, 'synthetic'))
    monkeypatch.setattr(em_tick, 'preflight', lambda *a: [])
    monkeypatch.setattr(em_tick.em_pool, 'available', lambda *a: False)
    seen = []

    def process(acct, *args, **kwargs):
        seen.append(lock_state(state_dir))
        return {'account': acct['slug'], 'status': 'completed'}
    monkeypatch.setattr(em_tick, 'process_account', process)
    monkeypatch.setattr(sys, 'argv', ['em_tick.py', '--config', str(registry)])
    return SimpleNamespace(state_dir=state_dir, seen=seen)


def test_the_tick_holds_the_writer_lock_while_it_processes(tick_main, capsys):
    assert em_tick.main() == 0
    assert tick_main.seen == ['busy'], 'a catch-up started during the tick would not be refused'
    assert lock_state(tick_main.state_dir) == 'free', 'the lock is released when the tick ends'


def test_the_tick_refuses_while_a_catchup_holds_the_lock(tick_main, capsys):
    with another_writer(tick_main.state_dir):
        code = em_tick.main()
    report = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code != 0 and not tick_main.seen, 'the tick processed accounts while another writer held the state'
    assert 'WriterBusy' in report['error'] and report['status'] == 'failed'


def test_dry_tick_takes_no_lock_and_creates_nothing(tick_main, monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', sys.argv + ['--dry'])
    assert em_tick.main() == 0
    assert not tick_main.state_dir.exists()
