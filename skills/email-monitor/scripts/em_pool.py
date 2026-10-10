#!/usr/bin/env python3
"""email-monitor -> schedule-reminder base adapter (the ONLY way the pool is touched).

email-monitor is the base's designated downstream #2. The personal-affairs memory pool IS the
schedule-reminder base. This module ONLY shells out to `reminder.py <verb> --json` and parses
stdout JSON. It NEVER reads the .db, builds SQL, or imports base internals (ARCHITECTURE §2.4,
anti-patterns #1/#2).

Guarantees enforced here:
  - source message replay returns the retained result without changing it
  - account-scoped thread identity; cross-thread merges require reviewed, expiring rules
  - consolidation aliases route subsequent messages to the retained obligation
  - ext namespace strictly x_email_monitor_*  (additive deep-merge, never overwrite blob)
  - state changes only via transition/done/block  (update on state -> ERR_USE_TRANSITION)
  - source always "email-monitor"

Usage (library import preferred; CLI for tests):
  python em_pool.py --reminder <path> --db <PRIVATE-path> upsert --message-id <id> --thread-key <k> \
      --title "Reply to X re Y" --kind task --due-at <utc> --account user1 --json
  python em_pool.py --reminder <path> [--db PATH] find-thread --thread-key <k>
Stdlib only.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import hashlib
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone, date
from email.utils import parseaddr

BASE_REL = ("schedule-reminder", "skills", "schedule-reminder", "scripts", "reminder.py")


def default_reminder_path():
    return os.path.expanduser(os.environ.get('EMAIL_MONITOR_REMINDER_CLI') or os.path.join("~", "CodesClaude", *BASE_REL))


def available(reminder=None):
    """True iff the schedule-reminder base is installed (its reminder.py is resolvable). The
    email -> pool co-op is OPTIONAL: when this is False, email-monitor still watches, classifies and
    alerts -- it just does not track items (or dated reminders) in the pool. Every pool write is
    gated on this so the skill runs standalone when the base skill is not installed."""
    reminder = reminder or default_reminder_path()
    return bool(reminder) and os.path.isfile(os.path.expanduser(reminder))


class PoolError(RuntimeError):
    def __init__(self, code, message, payload=None):
        super().__init__("%s: %s" % (code, message))
        self.code = code
        self.message = message
        self.payload = payload or {}


def _run(reminder, db, verb, args, retries=4, python=None):
    cmd = [python or sys.executable, '-B', reminder]
    if db:
        cmd += ["--db", db]
    cmd += ["--actor", "email-monitor", verb] + args
    last = None
    for attempt in range(retries):
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=60,
                           **({'creationflags': 0x08000000} if sys.platform == 'win32' else {}))
        out = (p.stdout or "").strip()
        err = (p.stderr or "").strip()
        if p.returncode == 0 and out:
            return json.loads(out.splitlines()[-1])
        # structured error on stderr
        payload = {}
        if err:
            try:
                payload = json.loads(err.splitlines()[-1])
            except Exception:
                payload = {"message": err}
        code = payload.get("error_code", "ERR_UNKNOWN")
        last = PoolError(code, payload.get("message", err or "no output"), payload)
        if code == "ERR_BUSY":            # exponential backoff on lock contention
            time.sleep(0.15 * (2 ** attempt))
            continue
        raise last
    raise last


def _items(reminder, db, python=None):
    """Read the owner contract, including manually maintained consolidation targets."""
    rows = []
    cursor = None
    while True:
        args = ["--limit", "5000"]
        if cursor:
            args += ["--cursor", cursor]
        res = _run(reminder, db, "list", args, **({"python": python} if python else {}))
        rows.extend(res.get('items', []))
        cursor = res.get("next_cursor")
        if not cursor:
            return rows


def _canonical(item, rows):
    by_id = {row['id']: row for row in rows}
    seen = set()
    while True:
        if item['id'] in seen:
            raise PoolError('ERR_MERGE_LINK', 'Cyclic consolidation link')
        seen.add(item['id'])
        parent = (item.get('ext') or {}).get('x_console_consolidation', {}).get('duplicate_of')
        if not parent:
            return item
        if parent not in by_id:
            raise PoolError('ERR_MERGE_LINK', 'Consolidation target missing')
        item = by_id[parent]


def find_thread(reminder, db, thread_key, account=None, python=None):
    rows = _items(reminder, db, python=python)
    for item in rows:
        ext = item.get('ext') or {}
        if thread_key and (account, thread_key) in _source_pairs(ext, 'thread'):
            return _canonical(item, rows)
    return None


def _norm(value):
    return ' '.join(str(value or '').split()).casefold()


def _account_aliases(ext):
    """The current registry binds a slug to its address; older ledgers used either form."""
    aliases = {ext.get('x_email_monitor_account')}
    address = ext.get('x_email_monitor_account_user')
    if isinstance(address, str) and address:
        aliases.add(address)
    return aliases


def _check_rule(rule):
    if not isinstance(rule, dict) or not all(isinstance(rule.get(k), str) and rule[k].strip()
                                              for k in ('account','sender','subject','until')):
        raise PoolError('ERR_MERGE_RULE', 'Incomplete reviewed consolidation rule')
    tokens = rule.get('contains', [])
    if not isinstance(tokens, list) or not tokens or any(not isinstance(token, str) or not token.strip() for token in tokens):
        raise PoolError('ERR_MERGE_RULE', 'Consolidation entity tokens must be a list of nonempty strings')
    try:
        date.fromisoformat(rule['until'])
    except (TypeError, ValueError) as exc:
        raise PoolError('ERR_MERGE_RULE', 'Invalid consolidation expiry') from exc


def _rule_targets(rule, ext):
    """Whether a rule's own account, sender and subject name this message.

    A merge needs all three to match, so a rule that does not name this message (or names no
    complete target at all) can never authorize merging it, whatever else is wrong with the rule.
    """
    if not isinstance(rule, dict) or not all(isinstance(rule.get(k), str) and rule[k].strip()
                                              for k in ('account', 'sender', 'subject')):
        return False
    return (rule['account'] in _account_aliases(ext)
            and _norm(rule['sender']) == _norm(parseaddr(ext.get('x_email_monitor_from', ''))[1])
            and _norm(rule['subject']) == _norm(ext.get('x_email_monitor_subject_raw')))


def _matches_reviewed(item, ext, text, skip_untargeted=False):
    """Only explicit, time-bounded account/sender/subject/entity rules cross threads.

    A defective rule is an error. With skip_untargeted (the write path), the error is raised only
    for a message the defective rule names. Otherwise one item's rule with an empty `contains`
    list makes every pool write for every account fail with ERR_MERGE_RULE. Skipping it for unrelated mail cannot lose a merge (it could not
    match them), and mail it does name still fails closed until the rule is repaired.
    """
    for rule in (item.get('ext') or {}).get('x_email_monitor_merge_rules', []):
        try:
            _check_rule(rule)
        except PoolError:
            if skip_untargeted and not _rule_targets(rule, ext):
                # The scheduled tick runs under pythonw, where sys.stderr is None.
                if sys.stderr is not None:
                    sys.stderr.write('email-monitor: skipped defective consolidation rule on pool item %s '
                                     '(it does not name this message)\n' % item.get('id'))
                continue
            raise
        expired = date.fromisoformat(rule['until']) < datetime.now(timezone.utc).date()
        if not expired and _rule_targets(rule, ext) and all(
                _norm(token) in _norm(text) for token in rule['contains']):
            return True
    return False


def _seen(ext):
    return {mid for mid in [ext.get('x_email_monitor_message_id'),ext.get('x_email_monitor_last_seen_msg_id'),
                            *ext.get('x_email_monitor_message_ids', [])] if mid}


def _source_pairs(ext, kind):
    """Keep each source identity bound to its account after cross-account consolidation."""
    key = 'x_email_monitor_' + kind + '_identities'
    if key in ext:
        identities = ext[key]
        if not isinstance(identities, list) or any(
                not isinstance(row, dict) or not isinstance(row.get('identity'), str)
                or not row['identity'] or row.get('account') is not None
                and not isinstance(row['account'], str) for row in identities):
            raise PoolError('ERR_IDENTITY', 'Invalid retained source identities')
        return {(row.get('account'), row['identity']) for row in identities}
    values = _seen(ext) if kind == 'message' else {
        value for value in [ext.get('x_email_monitor_thread_key'),
                           *ext.get('x_email_monitor_thread_keys', [])] if value}
    return {(ext.get('x_email_monitor_account'), value) for value in values}


def _retain_sources(ext, previous, account, message_id, thread_key):
    for kind, identity in (('message', message_id), ('thread', thread_key)):
        pairs = _source_pairs(previous, kind)
        if identity:
            pairs.add((account, identity))
        ext['x_email_monitor_' + kind + '_identities'] = [
            {'account': owner, 'identity': value}
            for owner, value in sorted(pairs, key=lambda pair: (pair[0] or '', pair[1]))]


@contextmanager
def _pool_lock(db):
    """Serialize the adapter's read/modify/write sequence across scheduler processes."""
    # One adapter lane per OS user also covers callers that omit the owner's database path.
    target = os.path.normcase(os.path.expanduser('~'))
    path = os.path.join(tempfile.gettempdir(), 'email-pool-' + hashlib.sha256(target.encode()).hexdigest() + '.lock')
    with open(path, 'a+b') as stream:
        if not stream.tell():
            stream.write(b'0'); stream.flush()
        deadline = time.monotonic() + 60
        while True:
            try:
                stream.seek(0)
                if sys.platform == 'win32':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise PoolError('ERR_BUSY', 'Timed out waiting for reminder adapter') from exc
                time.sleep(0.1)
        try:
            yield
        finally:
            stream.seek(0)
            if sys.platform == 'win32':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def upsert(reminder, db, message_id, thread_key, title, kind="task", due_at=None,
           description=None, priority=None, tags=None, project=None, ext_extra=None,
           progress=None, draft_id=None, match_text=None, idempotency_key=None, python=None):
    with _pool_lock(db):
        return _upsert(reminder, db, message_id, thread_key, title, kind, due_at, description,
                       priority, tags, project, ext_extra, progress, draft_id, match_text, idempotency_key, python)


def reconcile(action_record, rows):
    """Prove a pool action's prior disposition from a listing of the pool itself.

    confirmed: an item carries the action's key. Every keyed write records its key in the same
    reminder.py call that creates or updates the item (`add`/`update` with the key in --ext), so an
    item carrying the key is the write having landed.
    not_applied: no item carries the key, so no keyed write landed. Retrying is safe on top of that:
    the write is idempotent by source message, and a replay of a message the pool already retains
    only records the key on the retained item (the replay branch of _upsert).
    """
    key = action_record['idempotency_key']
    for row in rows:
        ext = row.get('ext') or {}
        if row.get('id') and key in [ext.get('x_email_monitor_action_key'),
                                     *ext.get('x_email_monitor_action_keys', [])]:
            return {'status': 'confirmed', 'idempotency_key': key, 'adapter': 'pool',
                    'receipt_id': str(_canonical(row, rows)['id'])}
    return {'status': 'not_applied', 'idempotency_key': key, 'adapter': 'pool',
            'evidence': 'the pool lists %d items and none carries this action key' % len(rows)}


def list_items(reminder, db, python=None):
    """The whole pool, read only (one listing serves every reconciliation in a tick)."""
    return _items(reminder, db, python=python)


def _upsert(reminder, db, message_id, thread_key, title, kind, due_at, description,
            priority, tags, project, ext_extra, progress, draft_id, match_text, idempotency_key, python):
    if not message_id:
        raise PoolError('ERR_IDENTITY', 'A source message ID is required')
    ext = {
        "x_email_monitor_message_id": message_id,
        "x_email_monitor_thread_key": thread_key,
    }
    if idempotency_key:
        ext["x_email_monitor_action_key"] = idempotency_key
    if draft_id:
        ext["x_email_monitor_draft_id"] = draft_id
    if ext_extra:
        ext.update({k if k.startswith("x_email_monitor_") else "x_email_monitor_" + k: v
                    for k, v in ext_extra.items()})

    def acknowledged(item, action):
        if idempotency_key is None:
            return {"item": item, "action": action}
        retained = (item.get("ext") or {}) if isinstance(item, dict) else {}
        keys = [retained.get("x_email_monitor_action_key"), *retained.get("x_email_monitor_action_keys", [])]
        if isinstance(item, dict) and item.get("id") and idempotency_key in keys:
            return {"status": "confirmed", "idempotency_key": idempotency_key,
                    "adapter": "pool", "receipt_id": str(item["id"])}
        return {"status": "uncertain", "idempotency_key": idempotency_key, "adapter": "pool"}

    rows = _items(reminder, db, python=python)
    account = ext.get('x_email_monitor_account')
    aliases = _account_aliases(ext)
    # Check every retained source message before following a thread or merge alias.
    for row in rows:
        prev = row.get('ext') or {}
        if any((alias, message_id) in _source_pairs(prev, 'message') for alias in aliases):
            retained = _canonical(row, rows)
            if idempotency_key and acknowledged(retained, 'replayed')['status'] != 'confirmed':
                previous = retained.get('ext') or {}
                keys = {key for key in [previous.get('x_email_monitor_action_key'),
                        *previous.get('x_email_monitor_action_keys', []), idempotency_key] if key}
                update = ['--id', retained['id'], '--ext', json.dumps({
                    'x_email_monitor_action_keys': sorted(keys)})]
                retained = _run(reminder, db, 'update', update,
                                **({'python': python} if python else {}))['item']
            return acknowledged(retained, 'replayed')
    matches = {}
    for row in rows:
        prev = row.get('ext') or {}
        same_thread = thread_key and thread_key != 'ref:unknown' and any(
            (alias, thread_key) in _source_pairs(prev, 'thread') for alias in aliases)
        if same_thread or _matches_reviewed(row, ext, match_text or title + '\n' + (description or ''),
                                            skip_untargeted=True):
            canonical = _canonical(row, rows)
            matches[canonical['id']] = canonical
    if len(matches) > 1:
        raise PoolError('ERR_MERGE_AMBIGUOUS', 'Multiple reviewed targets; resolve before creating work')
    existing = next(iter(matches.values()), None)
    if existing:
        # advance same item: merge ext (deep, additive) + bump msg count
        prev = (existing.get("ext") or {})
        _retain_sources(ext, prev, account, message_id, thread_key)
        if idempotency_key:
            ext['x_email_monitor_action_keys'] = sorted({key for key in [
                prev.get('x_email_monitor_action_key'), *prev.get('x_email_monitor_action_keys', []),
                idempotency_key] if key})
        n = int(prev.get("x_email_monitor_msg_count", 1) or 1) + 1
        ext["x_email_monitor_msg_count"] = n
        ext["x_email_monitor_last_seen_msg_id"] = message_id
        ext['x_email_monitor_message_ids'] = sorted(_seen(prev) | {message_id})
        ext['x_email_monitor_thread_keys'] = sorted({k for k in [prev.get('x_email_monitor_thread_key'), thread_key,
                                                        *prev.get('x_email_monitor_thread_keys', [])] if k})
        ext['x_email_monitor_latest_summary'] = title
        archived_notice = (existing.get('source') == 'email-monitor' and
                           existing.get('state') == 'cancelled' and
                           existing.get('kind') == 'event' and
                           isinstance(prev.get('x_email_monitor_notification_archive'), dict))
        if archived_notice and kind == 'task':
            # A new obligation in an archived information thread needs attention again.
            # Completed obligations and exact-message replays never take this path.
            existing = transition(reminder, db, existing['id'], 'pending', expect='cancelled',
                                  reason='New actionable mail in an archived information thread', python=python)
        if (kind == 'task' and existing.get('state') == 'pending' and
                isinstance(prev.get('x_email_monitor_notification_archive'), dict)):
            # Also finish marker cleanup if the earlier transition succeeded but update failed.
            ext['x_email_monitor_notification_archive'] = None
        upd = ["--id", existing["id"], "--ext", json.dumps(ext, ensure_ascii=False)]
        writable = existing.get('state') not in ('done','cancelled') and not prev.get('x_email_monitor_preserve_summary')
        if writable:
            upd += ['--set', 'title=' + title]
            if kind == 'task' and existing.get('kind') == 'event':
                upd += ['--set', 'kind=task']
            if description is not None:
                upd += ['--set', 'description=' + description]
        if writable and progress is not None:
            upd += ["--set", "progress=%d" % progress]
        if writable and priority is not None:
            upd += ["--set", "priority=%d" % priority]
        if writable and due_at:
            upd += ["--set", "due_at=%s" % due_at]
        item = _run(reminder, db, "update", upd, **({"python": python} if python else {}))["item"]
        return acknowledged(item, "merged")

    # New threads use an account-scoped key; the adapter lock covers the owner write.
    _retain_sources(ext, {}, account, message_id, thread_key)
    ext["x_email_monitor_msg_count"] = 1
    identity = json.dumps([account, thread_key if thread_key and thread_key != 'ref:unknown' else message_id])
    args = ["--title", title, "--kind", kind, "--source", "email-monitor",
            "--idempotency-key", idempotency_key or "email-monitor:thread:" + hashlib.sha256(identity.encode()).hexdigest(),
            "--ext", json.dumps(ext, ensure_ascii=False)]
    if due_at:
        args += ["--due-at", due_at]
    if description:
        args += ["--description", description]
    if priority is not None:
        args += ["--priority", str(priority)]
    if progress is not None:
        args += ["--progress", str(progress)]
    if tags:
        args += ["--tags", ",".join(tags)]
    if project:
        args += ["--project", project]
    item = _run(reminder, db, "add", args, **({"python": python} if python else {}))["item"]
    return acknowledged(item, "created")


def transition(reminder, db, item_id, to, reason=None, progress=None, expect=None, python=None):
    args = ["--id", item_id, "--to", to]
    if reason:
        args += ["--reason", reason]
    if progress is not None:
        args += ["--progress", str(progress)]
    if expect:
        args += ["--expect", expect]
    return _run(reminder, db, "transition", args, **({'python': python} if python else {}))["item"]


def mark_done(reminder, db, item_id, python=None):
    return _run(reminder, db, "done", ["--id", item_id], **({'python': python} if python else {}))["item"]


def mark_blocked(reminder, db, item_id, reason):
    return _run(reminder, db, "block", ["--id", item_id, "--reason", reason])["item"]


def due(reminder, db, now=None, lead=None, python=None):
    args = []
    if now:
        args += ["--now", now]
    if lead:
        args += ["--lead", lead]
    return _run(reminder, db, "due", args, **({'python': python} if python else {}))


def _cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reminder", default=default_reminder_path())
    ap.add_argument("--db", default=None, help="writes require an explicit database in a verified PRIVATE companion")
    sub = ap.add_subparsers(dest="cmd", required=True)

    u = sub.add_parser("upsert")
    u.add_argument("--message-id", required=True)
    u.add_argument("--thread-key", required=True)
    u.add_argument("--title", required=True)
    u.add_argument("--account")
    u.add_argument("--kind", default="task")
    u.add_argument("--due-at")
    u.add_argument("--description")
    u.add_argument("--priority", type=int)
    u.add_argument("--progress", type=int)
    u.add_argument("--project")
    u.add_argument("--tags")
    u.add_argument("--draft-id")
    u.add_argument("--json", action="store_true")

    f = sub.add_parser("find-thread")
    f.add_argument("--thread-key", required=True)
    f.add_argument("--account")

    t = sub.add_parser("transition")
    t.add_argument("--id", required=True)
    t.add_argument("--to", required=True)
    t.add_argument("--reason")
    t.add_argument("--progress", type=int)

    d = sub.add_parser("done")
    d.add_argument("--id", required=True)

    b = sub.add_parser("block")
    b.add_argument("--id", required=True)
    b.add_argument("--reason", required=True)

    a = ap.parse_args()
    try:
        if a.cmd != "find-thread":
            from em_runtime import prove_private
            try:
                if not a.db:
                    raise ValueError("supply --db in a verified PRIVATE companion before writing")
                prove_private(a.db)
            except ValueError as error:
                raise PoolError('ERR_DATA_BOUNDARY', str(error)) from error
        if a.cmd == "upsert":
            res = upsert(a.reminder, a.db, a.message_id, a.thread_key, a.title, a.kind,
                         a.due_at, a.description, a.priority,
                         a.tags.split(",") if a.tags else None, a.project,
                         ext_extra={'account':a.account} if a.account else None,
                         progress=a.progress, draft_id=a.draft_id)
            print(json.dumps(res, ensure_ascii=False))
        elif a.cmd == "find-thread":
            print(json.dumps(find_thread(a.reminder, a.db, a.thread_key, account=a.account), ensure_ascii=False))
        elif a.cmd == "transition":
            print(json.dumps(transition(a.reminder, a.db, a.id, a.to, a.reason, a.progress),
                             ensure_ascii=False))
        elif a.cmd == "done":
            print(json.dumps(mark_done(a.reminder, a.db, a.id), ensure_ascii=False))
        elif a.cmd == "block":
            print(json.dumps(mark_blocked(a.reminder, a.db, a.id, a.reason), ensure_ascii=False))
        return 0
    except PoolError as e:
        print(json.dumps({"ok": False, "error_code": e.code, "message": e.message}),
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(_cli())
