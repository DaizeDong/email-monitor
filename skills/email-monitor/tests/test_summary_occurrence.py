"""Real reminder business state and receipt producer, synthetic transport only."""
import json
import os
from pathlib import Path
import sys
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = ROOT / 'schedule-reminder/skills/schedule-reminder/scripts'
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import store
import relay
import notification_receipts as receipts
import em_summary as em


@pytest.fixture
def rig(monkeypatch):
    scratch = Path(os.environ.get('T15_REVIEW_SCRATCH', ROOT / 'artifacts/T15-backup-repair'))
    path = scratch / ('synthetic-email-' + uuid.uuid4().hex)
    path.mkdir(parents=True)
    db = path / 'reminder.db'
    for key, value in {'SCHEDULE_DB_PATH':db, 'AGENT_CENTER_CONFIG':path/'registry.json',
                       'SCHEDULE_NOTIFICATION_CLIENT':SCRIPTS/'notification_client.py',
                       'SCHEDULE_RELAY_PY':SCRIPTS/'relay.py',
                       'EMAIL_MONITOR_NOTIFIER':path/'absent.py'}.items():
        monkeypatch.setenv(key, str(value))
    (path/'registry.json').write_text(json.dumps({'streams':{'mail':{'channel_id':'10001'}},
        'reader':{'bot_token':'SYNTHETIC_TOKEN'}, 'big_brother':{'user_id':'20002'}}))
    (path/'config.json').write_text('{}')
    store.init_db(db_path=str(db))
    def due(day):
        return store.add_item('synthetic daily summary', kind='event', source='email-monitor',
            due_at='2026-09-%sT12:00:00Z' % day, ext={'x_email_monitor_kind':'daily-summary'}, db_path=str(db))
    item = due('18')
    monkeypatch.setattr(sys, 'argv', ['em_summary.py','--config',str(path/'config.json'),
        '--db',str(db),'--reminder',str(SCRIPTS/'reminder.py'),'--now','2026-09-18T13:00:00Z','--run-id','same-run'])
    monkeypatch.setattr(em, 'assemble', lambda *a: '每日邮件汇总：合成原始待办')
    monkeypatch.setattr(em.em_pool, 'due', lambda *a: {'items':store.due_items(now='2026-09-30T13:00:00Z',db_path=str(db))})
    monkeypatch.setattr(em.em_pool, 'mark_done', lambda *a: store.transition(a[-1], 'done', db_path=str(db)))
    monkeypatch.setattr(em.em_pool, '_run', lambda *a, **kw: {})
    calls = []
    monkeypatch.setattr(relay, '_post_bot', lambda *a: calls.append(a[1]) or False)
    import subprocess, llmcall, llmcall.process
    def blocked(*a, **kw): raise AssertionError('real execution forbidden')
    monkeypatch.setattr(subprocess, 'Popen', blocked)
    monkeypatch.setattr(llmcall, 'call', blocked)
    monkeypatch.setattr(llmcall.process, 'run', blocked)
    return db, item, due, calls


def test_unknown_same_invocation_and_nextday_keep_one_frozen_event(rig, monkeypatch):
    db, item, add, calls = rig
    assert em.main() == 1
    assert store.get_item(item['id'], db_path=str(db))['state'] == 'pending'
    assert em.main() == 1
    tomorrow = add('19')
    monkeypatch.setattr(em, 'assemble', lambda *a: '每日邮件汇总：第二天新待办')
    sys.argv[-1] = 'next-day-run'
    sys.argv[sys.argv.index('--now') + 1] = '2026-09-19T13:00:00Z'
    assert em.main() == 1
    with receipts._connection(str(db)) as conn:
        rows = [json.loads(r[0]) for r in conn.execute('SELECT receipt FROM notification_receipts')]
    assert len(calls) == 1 and len(rows) == 1 and rows[0]['state'] == 'uncertain'
    assert rows[0]['attempts'] == 1
    assert store.get_item(tomorrow['id'], db_path=str(db))['state'] == 'pending'


def test_presend_retry_uses_original_digest_and_completes_only_after_sent(rig, monkeypatch):
    db, item, add, calls = rig
    registry = Path(os.environ['AGENT_CENTER_CONFIG'])
    saved = registry.read_text(); registry.write_text('{}')
    assert em.main() == 1 and not calls
    monkeypatch.setattr(em, 'assemble', lambda *a: '每日邮件汇总：后来变化')
    registry.write_text(saved)
    monkeypatch.setattr(relay, '_post_bot', lambda *a: calls.append(a[1]) or True)
    assert em.main() == 0
    assert len(calls) == 1 and '合成原始待办' in calls[0] and '后来变化' not in calls[0]
    assert store.get_item(item['id'], db_path=str(db))['state'] == 'done'
    add('19')
    assert em.main() == 0 and len(calls) == 1


def test_explicit_db_owns_both_occurrence_and_notification(rig, monkeypatch):
    db, item, _, calls = rig
    other = db.parent/'other.db'; store.init_db(db_path=str(other))
    monkeypatch.setenv('SCHEDULE_DB_PATH', str(other))
    assert em.main() == 1
    with receipts._connection(str(db)) as conn:
        assert conn.execute('SELECT count(*) FROM notification_receipts').fetchone()[0] == 1
    with receipts._connection(str(other)) as conn:
        assert conn.execute('SELECT count(*) FROM notification_receipts').fetchone()[0] == 0
    assert len(calls) == 1


def test_legacy_unassociated_unknown_receipt_requires_reconciliation(rig):
    db, item, _, calls = rig
    event = receipts.Event('email-monitor','synthetic-legacy-digest','digest','daily','mail')
    prior = receipts.deliver(event, '每日邮件汇总：原来的合成待办', db_path=str(db))
    assert prior['state'] == 'uncertain'
    assert em.main() == 1 and len(calls) == 1
    assert store.get_item(item['id'], db_path=str(db))['state'] == 'pending'


def test_reentry_after_frozen_business_write_before_claim_uses_original_payload(rig, monkeypatch):
    db, item, _, calls = rig
    owner = em._business_owner
    store_owner, receipt_owner, real_client = owner()
    class UnavailableClient:
        def submit(self, *args, **kw): raise RuntimeError('synthetic pre-claim loss')
        transport_options = staticmethod(real_client.transport_options)
    monkeypatch.setattr(em, '_business_owner', lambda: (store_owner, receipt_owner, UnavailableClient()))
    assert em.main() == 1 and not calls
    monkeypatch.setattr(em, '_business_owner', owner)
    monkeypatch.setattr(em, 'assemble', lambda *a: '每日邮件汇总：新的合成待办')
    monkeypatch.setattr(relay, '_post_bot', lambda *a: calls.append(a[1]) or True)
    assert em.main() == 0 and len(calls) == 1
    assert '合成原始待办' in calls[0] and '新的合成待办' not in calls[0]


def test_completed_business_item_cannot_hide_unknown_delivery(rig):
    db, item, add, calls = rig
    assert em.main() == 1
    store.transition(item['id'], 'done', db_path=str(db))
    add('19')
    sys.argv[-1] = 'new-run'
    sys.argv[sys.argv.index('--now') + 1] = '2026-09-19T13:00:00Z'
    assert em.main() == 1 and len(calls) == 1


def test_changed_business_event_reference_cannot_bypass_unknown(rig):
    db, item, _, calls = rig
    assert em.main() == 1
    stored = store.get_item(item['id'], db_path=str(db))['ext'][em._SUMMARY]
    stored['run_id'] = 'synthetic-different-run'
    store.update_item(item['id'], ext={em._SUMMARY:stored}, db_path=str(db))
    assert em.main() == 1 and len(calls) == 1


def receipt_rows(db):
    with receipts._connection(str(db)) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 4
        return [json.loads(row[0]) for row in conn.execute('SELECT receipt FROM notification_receipts')]


def crash_before_claim(monkeypatch):
    owner = em._business_owner
    store_owner, receipt_owner, client = owner()

    class InterruptedClient:
        transport_options = staticmethod(client.transport_options)

        def submit(self, *args, **kwargs):
            raise KeyboardInterrupt('synthetic process loss before receipt claim')

    with monkeypatch.context() as crash:
        crash.setattr(em, '_business_owner', lambda: (store_owner, receipt_owner, InterruptedClient()))
        with pytest.raises(KeyboardInterrupt):
            em.main()


@pytest.mark.parametrize('business_state', ['pending', 'done'])
def test_preclaim_crash_recovers_frozen_digest_before_new_day(rig, monkeypatch, business_state):
    db, item, add, calls = rig
    crash_before_claim(monkeypatch)
    frozen = store.get_item(item['id'], db_path=str(db))['ext'][em._SUMMARY]
    assert receipt_rows(db) == [] and calls == []
    if business_state == 'done':
        store.transition(item['id'], 'done', db_path=str(db))
    tomorrow = add('19')
    sys.argv[-1] = 'recovery-run'
    sys.argv[sys.argv.index('--now') + 1] = '2026-09-19T13:00:00Z'
    monkeypatch.setattr(em, 'assemble', lambda *a: 'Synthetic next-day digest')
    monkeypatch.setattr(relay, '_post_bot', lambda *a: calls.append(a[1]) or True)

    assert em.main() == 0
    first = receipt_rows(db)
    assert len(first) == 1
    assert (first[0]['event_id'], first[0]['run_id'], first[0]['state'], first[0]['attempts']) == (
        frozen['event_id'], frozen['run_id'], 'sent', 1)
    assert calls == [frozen['digest']]
    completed = store.get_item(item['id'], db_path=str(db))
    assert completed['state'] == 'done'
    assert completed['ext'][em._SUMMARY] == dict(frozen, invocations=['same-run', 'recovery-run'])
    assert frozen['next_due_at'] == '2026-09-19T12:00:00Z'
    assert store.get_item(tomorrow['id'], db_path=str(db))['state'] == 'pending'
    with receipts._connection(str(db)) as conn:
        assert conn.execute('SELECT count(*) FROM items WHERE substr(due_at, 1, 10)=?',
                            ('2026-09-20',)).fetchone()[0] == 0

    assert em.main() == 0
    assert receipt_rows(db) == first and calls == [frozen['digest']]
    sys.argv[-1] = 'next-occurrence-run'
    assert em.main() == 0
    later = receipt_rows(db)
    assert len(later) == 2 and first[0] in later
    assert calls == [frozen['digest'], 'Synthetic next-day digest']
    assert store.get_item(tomorrow['id'], db_path=str(db))['state'] == 'done'


@pytest.mark.parametrize('prior_receipt', ['absent', 'uncertain'])
@pytest.mark.parametrize('invocation', ['same-run', 'new-run'])
def test_cancelled_frozen_digest_requires_reconciliation(rig, monkeypatch, capsys,
                                                        prior_receipt, invocation):
    db, item, add, calls = rig
    if prior_receipt == 'absent':
        crash_before_claim(monkeypatch)
    else:
        assert em.main() == 1
    before = receipt_rows(db)
    call_count = len(calls)
    store.transition(item['id'], 'cancelled', db_path=str(db))
    tomorrow = add('19')
    sys.argv[-1] = invocation
    sys.argv[sys.argv.index('--now') + 1] = '2026-09-19T13:00:00Z'
    monkeypatch.setattr(relay, '_post_bot', lambda *a: calls.append(a[1]) or True)

    assert em.main() == 1
    assert 'cancelled_summary_requires_reconciliation' in capsys.readouterr().err
    assert receipt_rows(db) == before and len(calls) == call_count
    assert store.get_item(item['id'], db_path=str(db))['state'] == 'cancelled'
    assert store.get_item(tomorrow['id'], db_path=str(db))['state'] == 'pending'


@pytest.mark.parametrize('cancelled', [False, True])
def test_sent_receipt_survives_interrupted_business_completion(rig, monkeypatch, cancelled):
    db, item, add, calls = rig
    monkeypatch.setattr(relay, '_post_bot', lambda *a: calls.append(a[1]) or True)

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt('synthetic loss after sent receipt, before business completion')

    with monkeypatch.context() as crash:
        crash.setattr(store, 'transition', interrupted)
        with pytest.raises(KeyboardInterrupt):
            em.main()
    before = receipt_rows(db)
    assert len(before) == 1 and before[0]['state'] == 'sent' and len(calls) == 1
    assert store.get_item(item['id'], db_path=str(db))['state'] == 'pending'
    tomorrow = add('19')
    if cancelled:
        store.transition(item['id'], 'cancelled', db_path=str(db))
    else:
        sys.argv[-1] = 'new-run'
    sys.argv[sys.argv.index('--now') + 1] = '2026-09-19T13:00:00Z'
    monkeypatch.setattr(em, 'assemble', lambda *a: 'Synthetic later digest')

    assert em.main() == (1 if cancelled else 0)
    assert receipt_rows(db) == before and len(calls) == 1
    assert store.get_item(item['id'], db_path=str(db))['state'] == ('cancelled' if cancelled else 'done')
    assert store.get_item(tomorrow['id'], db_path=str(db))['state'] == 'pending'
