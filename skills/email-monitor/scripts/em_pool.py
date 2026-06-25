#!/usr/bin/env python3
"""email-monitor -> schedule-reminder base adapter (the ONLY way the pool is touched).

email-monitor is the base's designated downstream #2. The personal-affairs memory pool IS the
schedule-reminder base. This module ONLY shells out to `reminder.py <verb> --json` and parses
stdout JSON. It NEVER reads the .db, builds SQL, or imports base internals (ARCHITECTURE §2.4,
anti-patterns #1/#2).

Guarantees enforced here:
  - idempotency_key = "email-monitor:<Message-ID>"  (gate 1: same mail re-scan -> same item id)
  - thread_key semantic merge (gate 2: later mail in a thread advances, never duplicates)
  - ext namespace strictly x_email_monitor_*  (additive deep-merge, never overwrite blob)
  - state changes only via transition/done/block  (update on state -> ERR_USE_TRANSITION)
  - source always "email-monitor"

Usage (library import preferred; CLI for tests):
  python em_pool.py --reminder <path> [--db PATH] upsert --message-id <id> --thread-key <k> \
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

BASE_REL = ("schedule-reminder", "skills", "schedule-reminder", "scripts", "reminder.py")


def default_reminder_path():
    return os.path.expanduser(os.path.join("~", "CodesSelf", *BASE_REL))


class PoolError(RuntimeError):
    def __init__(self, code, message, payload=None):
        super().__init__("%s: %s" % (code, message))
        self.code = code
        self.message = message
        self.payload = payload or {}


def _run(reminder, db, verb, args, retries=4):
    cmd = [sys.executable, reminder]
    if db:
        cmd += ["--db", db]
    cmd += ["--actor", "email-monitor", verb] + args
    last = None
    for attempt in range(retries):
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
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


def find_thread(reminder, db, thread_key):
    """Return existing item dict for a thread_key, or None. Scans email-monitor source only."""
    cursor = None
    while True:
        args = ["--source", "email-monitor", "--limit", "100"]
        if cursor:
            args += ["--cursor", cursor]
        res = _run(reminder, db, "list", args)
        for it in res.get("items", []):
            ext = it.get("ext") or {}
            if ext.get("x_email_monitor_thread_key") == thread_key:
                return it
        cursor = res.get("next_cursor")
        if not cursor:
            return None


def upsert(reminder, db, message_id, thread_key, title, kind="task", due_at=None,
           description=None, priority=None, tags=None, project=None, ext_extra=None,
           progress=None, draft_id=None):
    """Idempotent create/merge. thread_key match -> update existing; else add new.

    Gate 1 (Message-ID idempotency) is handled by the base UPSERT on idempotency_key.
    Gate 2 (thread merge) is handled here: an existing thread item is advanced, not duplicated.
    """
    ext = {
        "x_email_monitor_message_id": message_id,
        "x_email_monitor_thread_key": thread_key,
    }
    if draft_id:
        ext["x_email_monitor_draft_id"] = draft_id
    if ext_extra:
        ext.update({k if k.startswith("x_email_monitor_") else "x_email_monitor_" + k: v
                    for k, v in ext_extra.items()})

    existing = find_thread(reminder, db, thread_key)
    if existing:
        # advance same item: merge ext (deep, additive) + bump msg count
        prev = (existing.get("ext") or {})
        n = int(prev.get("x_email_monitor_msg_count", 1) or 1) + 1
        ext["x_email_monitor_msg_count"] = n
        ext["x_email_monitor_last_seen_msg_id"] = message_id
        upd = ["--id", existing["id"], "--ext", json.dumps(ext, ensure_ascii=False)]
        if progress is not None:
            upd += ["--set", "progress=%d" % progress]
        if due_at:
            upd += ["--set", "due_at=%s" % due_at]
        item = _run(reminder, db, "update", upd)["item"]
        return {"item": item, "action": "merged"}

    # new item
    ext["x_email_monitor_msg_count"] = 1
    args = ["--title", title, "--kind", kind, "--source", "email-monitor",
            "--idempotency-key", "email-monitor:%s" % message_id,
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
    item = _run(reminder, db, "add", args)["item"]
    return {"item": item, "action": "created"}


def transition(reminder, db, item_id, to, reason=None, progress=None, expect=None):
    args = ["--id", item_id, "--to", to]
    if reason:
        args += ["--reason", reason]
    if progress is not None:
        args += ["--progress", str(progress)]
    if expect:
        args += ["--expect", expect]
    return _run(reminder, db, "transition", args)["item"]


def mark_done(reminder, db, item_id):
    return _run(reminder, db, "done", ["--id", item_id])["item"]


def mark_blocked(reminder, db, item_id, reason):
    return _run(reminder, db, "block", ["--id", item_id, "--reason", reason])["item"]


def due(reminder, db, now=None, lead=None):
    args = []
    if now:
        args += ["--now", now]
    if lead:
        args += ["--lead", lead]
    return _run(reminder, db, "due", args)


def _cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reminder", default=default_reminder_path())
    ap.add_argument("--db", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    u = sub.add_parser("upsert")
    u.add_argument("--message-id", required=True)
    u.add_argument("--thread-key", required=True)
    u.add_argument("--title", required=True)
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
        if a.cmd == "upsert":
            res = upsert(a.reminder, a.db, a.message_id, a.thread_key, a.title, a.kind,
                         a.due_at, a.description, a.priority,
                         a.tags.split(",") if a.tags else None, a.project,
                         progress=a.progress, draft_id=a.draft_id)
            print(json.dumps(res, ensure_ascii=False))
        elif a.cmd == "find-thread":
            print(json.dumps(find_thread(a.reminder, a.db, a.thread_key), ensure_ascii=False))
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
