"""One writer per state directory: em_catchup against a running tick, and the tick's own lock.

The lock is exercised through its documented protocol (byte 0 of <state_dir>/.writer.lock, held
through another open handle, which the OS treats as another holder: Windows byte-range locks belong
to the handle, flock to the open file description), so a writer that ignores it is caught.
The offline suite forbids child processes, so the other holder lives in this process.
Synthetic data only.
"""
from contextlib import contextmanager
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
from private_storage_helpers import make_repository

ACCOUNT = 'user1@example.com'
FIX = json.loads((Path(__file__).parent / 'reliability.json').read_text(encoding='utf-8'))
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


@pytest.fixture
def tick_main(tmp_path, monkeypatch):
    state_dir = tmp_path / 'state'
    cfg = {'schema_version': 1, 'accounts': [{'slug': 'user1', 'user': ACCOUNT}],
           'runtime': {'python': sys.executable}, 'classifier': {'mode': 'heuristic'}, 'draft': FIX['draft']}
    registry = tmp_path / 'registry.json'
    registry.write_text(json.dumps(cfg), encoding='utf-8')
    storage = {'state_dir': str(state_dir), 'db': str(tmp_path / 'pool.db'), 'log': str(tmp_path / 'log.txt')}
    monkeypatch.setattr(em_tick.em_runtime, 'storage_config', lambda *a, **k: dict(storage))
    monkeypatch.setattr(em_tick.em_runtime, 'private_path', Path)
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


def test_the_tick_refuses_while_a_catchup_holds_the_lock(tick_main, capsys):
    with another_writer(tick_main.state_dir):
        code = em_tick.main()
    report = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code != 0 and not tick_main.seen, 'the tick processed accounts while another writer held the state'
    assert 'WriterBusy' in report['error'] and report['status'] == 'failed'
