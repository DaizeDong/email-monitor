#!/usr/bin/env python3
"""email-monitor daily summary worker (content side; due=signal / worker=content, decoupled).

The base `tick` only emits a trigger line and cannot carry a body. This worker freezes a due
occurrence and its plain-text digest in the existing reminder item's business extension, using
the reminder owner's transaction. The shared receipt producer exclusively owns delivery state.
Only confirmed delivery completes the occurrence and re-arms tomorrow by local-calendar
recompute (not naive +24h, which drifts an hour across DST). An unresolved frozen occurrence
takes precedence over later due additions; scheduler run IDs remain associated with it.

Digest sections (Chinese): 待处理 / 等对方回复 / 草稿已备等你点发送 / 今日新增
New tasks today / Archived today (count). No bodies, no PII beyond local titles already in the pool.

Usage:
  python em_summary.py --config <registry.json> [--db PATH] [--reminder PATH] [--now ISO] [--dry]
Stdlib only.
"""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path
from datetime import timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import em_pool   # noqa: E402
import em_alert  # noqa: E402

try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    NY = timezone(timedelta(hours=-4))


def next_summary_utc(local_time="08:00", now=None):
    """Tomorrow's summary anchor at local_time NY, returned UTC RFC3339 (DST-correct)."""
    base = now or datetime.datetime.now(tz=NY)
    if base.tzinfo is None:
        base = base.replace(tzinfo=NY)
    base = base.astimezone(NY)
    hh, mm = (int(x) for x in local_time.split(":"))
    tomorrow = (base + timedelta(days=1)).replace(hour=hh, minute=mm, second=0, microsecond=0)
    return tomorrow.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def assemble(reminder, db):
    """Pull open/active items from the base and bucket them into a plain-text digest."""
    res = em_pool._run(reminder, db, "list", ["--source", "email-monitor", "--active", "--limit", "200"])
    items = res.get("items", [])
    important, awaiting, drafted, newtoday = [], [], [], []
    today = datetime.datetime.now(tz=timezone.utc).date().isoformat()
    for it in items:
        ext = it.get("ext") or {}
        st = it.get("state")
        pr = it.get("priority") or 9
        # pool titles are Chinese now (em_tick.derive_title); the old ASCII-only filter here would
        # strip every one of them back down to an empty row.
        title = (it.get("title") or "").strip()
        if st == "blocked":
            awaiting.append(title)
        elif ext.get("x_email_monitor_draft_id"):
            drafted.append(title)
        elif pr <= 4:
            important.append(title)
        if (it.get("created_at") or "").startswith(today):
            newtoday.append(title)

    lines = ["📬 每日邮件汇总 (%s)" % today, ""]
    def section(name, rows):
        lines.append("%s (%d):" % (name, len(rows)))
        for r in rows[:15]:
            lines.append("  - " + r)
        if not rows:
            lines.append("  (无)")
        lines.append("")
    section("待处理", important)
    section("等对方回复", awaiting)
    section("草稿已备,等你点发送", drafted)
    section("今日新增", newtoday)
    return "\n".join(lines).rstrip()


def _business_owner():
    """Use the receipt producer's existing DB and transaction owner, without a new ledger."""
    client = em_alert._notification_client()
    sys.path.insert(0, str(Path(client.__file__).resolve().parent))
    import notification_receipts
    return notification_receipts.store, notification_receipts, client


_SUMMARY = 'x_email_monitor_summary'


def freeze_occurrence(store, receipts, db, invocation, digest, now, local_time):
    """Freeze one business occurrence before delivery, including aliases for scheduler reentry.

    The existing store owns the short IMMEDIATE transaction and audit stream. Only
    business association lives in item.ext; notification state stays in its producer.
    Older unresolved occurrences take precedence over new due additions.
    """
    with receipts._connection(db) as conn, store._Tx(conn):
        items = [store._row_to_item(row) for row in conn.execute(
            'SELECT * FROM items WHERE source=? ORDER BY due_at, created_at, id', ('email-monitor',))]
        items = [it for it in items if (it.get('ext') or {}).get('x_email_monitor_kind')
                 in ('daily-summary', 'summary-manual')]
        known = set()
        for item in items:
            frozen = (item.get('ext') or {}).get(_SUMMARY)
            if frozen:
                expected = 'email-summary:' + json.dumps([(item['id'], frozen['due_at'])], separators=(',', ':'))
                if (frozen['run_id'] != expected or
                        frozen['event_id'] != receipts.event_id(expected, 'digest', 'daily')):
                    raise RuntimeError('summary_association_changed_requires_reconciliation')
                known.add(frozen['event_id'])
        # Old consumers did not freeze payloads. Never invent a new event to bypass
        # an unresolved receipt left by one of those consumers.
        # A frozen association can precede its receipt claim. Only shared sent
        # evidence closes it; business done alone does not establish delivery.
        unresolved = known.copy()
        for row in conn.execute('SELECT event_id FROM notification_receipts'):
            prior = receipts._read(conn, row[0])
            if prior['owner'] == 'email-monitor' and prior['phase'] == 'digest':
                if prior['state'] == 'sent':
                    unresolved.discard(prior['event_id'])
                elif prior['event_id'] not in known:
                    raise RuntimeError('legacy_digest_requires_reconciliation')
        alias = [it for it in items if invocation and invocation in
                 (it.get('ext') or {}).get(_SUMMARY, {}).get('invocations', [])]
        pending = [it for it in items if (it.get('ext') or {}).get(_SUMMARY) and
                   (it['state'] in store.ACTIVE_STATES or it['ext'][_SUMMARY]['event_id'] in unresolved)]
        due = [it for it in items if it['state'] in store.ACTIVE_STATES and
               ((it.get('due_at') and store.parse_dt(it['due_at']) <= now) or
                (invocation and it.get('idempotency_key') == 'email-monitor:summary-run:' + invocation))]
        selected = alias or pending or due
        if not selected:
            return None
        item = selected[0]
        if item['state'] == 'cancelled':
            raise RuntimeError('cancelled_summary_requires_reconciliation')
        ext = dict(item.get('ext') or {})
        frozen = ext.get(_SUMMARY)
        if frozen is None:
            run_id = 'email-summary:' + json.dumps([(item['id'], item.get('due_at'))], separators=(',', ':'))
            event_id = receipts.event_id(run_id, 'digest', 'daily')
            if receipts._read(conn, event_id) is not None:
                raise RuntimeError('legacy_digest_requires_reconciliation')
            frozen = {'run_id': run_id, 'event_id': event_id, 'digest': digest,
                      'due_at': item.get('due_at'), 'invocations': [],
                      'next_due_at': next_summary_utc(local_time, now)}
        if frozen['due_at'] != item.get('due_at'):
            raise RuntimeError('summary_occurrence_changed_requires_reconciliation')
        if invocation and invocation not in frozen['invocations']:
            frozen['invocations'].append(invocation)
        ext[_SUMMARY] = frozen
        conn.execute('UPDATE items SET ext=?, updated_at=? WHERE id=?',
                     (json.dumps(ext, ensure_ascii=False), store.resolve_now(), item['id']))
        store._append_event(conn, item['id'], 'email-monitor', 'summary_associated',
                            payload={'event_id': frozen['event_id']})
        return item['id'], frozen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--db", default=None)
    ap.add_argument("--reminder", default=em_pool.default_reminder_path())
    ap.add_argument("--now", default=None)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--run-id", help="stable owner run ID when no due summary occurrence exists")
    a = ap.parse_args()

    with open(a.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    local_time = cfg.get("daily_summary", {}).get("local_time", "08:00")

    digest = assemble(a.reminder, a.db)
    if a.dry:
        print(digest)
        return 0
    try:
        store, receipts, client = _business_owner()
        now = store.parse_dt(store.resolve_now(a.now))
        invocation = a.run_id or os.environ.get('TASK_RUN_ID') or os.environ.get('SCHEDULE_RUN_ID')
        selected = freeze_occurrence(store, receipts, a.db, invocation, digest, now, local_time)
        if selected is None:
            if not invocation:
                raise RuntimeError('stable_run_id_or_due_occurrence_required')
            store.add_item('每日邮件汇总', kind='event', source='email-monitor',
                           idempotency_key='email-monitor:summary-run:' + invocation,
                           ext={'x_email_monitor_kind': 'summary-manual'}, db_path=a.db)
            selected = freeze_occurrence(store, receipts, a.db, invocation, digest, now, local_time)
        item_id, frozen = selected
        cmd = em_alert._egress_cmd()
        if not cmd:
            raise RuntimeError('no relay available; explicit notifier target unavailable')
        receipt = client.submit('email-monitor', frozen['run_id'], 'digest', 'daily', 'mail',
            frozen['digest'], language='preserve', retry_failed=True, db_path=a.db,
            **client.transport_options(cmd, 'mail'))
        if receipt['state'] != 'sent':
            raise RuntimeError(client.detail(receipt))
        item = store.get_item(item_id, db_path=a.db)
        if item['state'] != 'done':
            store.transition(item_id, 'done', actor='email-monitor', db_path=a.db)
        nxt = frozen['next_due_at']
        store.add_item('每日邮件汇总', kind='event', due_at=nxt, source='email-monitor',
                       idempotency_key='email-monitor:daily-summary:' + nxt[:10],
                       ext={'x_email_monitor_kind': 'daily-summary'}, db_path=a.db)
    except Exception as e:
        print("relay failed: %s" % e, file=sys.stderr)
        return 1
    print('summary sent; next armed for %s' % nxt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
