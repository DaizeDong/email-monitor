#!/usr/bin/env python3
"""Operator recovery for keyed actions that a broken helper left undelivered.

Two subcommands, both run by hand after the cause is fixed:

``alerts`` gathers every alert that never completed (per-account ``alert`` rows and the
``alert`` step of unfinished daily summaries) and delivers them as ONE consolidated catch-up
message through the normal relay (the relay splits it into the fewest Discord-sized parts).
Re-sending each stale alert on its own would push many old notifications at once; closing
them without delivery would delete messages the owner never saw. Only after the relay
confirms the catch-up is each member marked ``completed`` with a ``catch_up`` receipt that
names the catch-up key and the relay's message ids, so the ledger still shows how and when it
was delivered. A journal entry is written before the send: an interrupted catch-up is
refused on the next run instead of being sent twice.

``requeue`` marks never-applied rows of an idempotent adapter (for example ``topic_label``,
where adding a label twice is a no-op) as ``failed`` with a ``not_applied`` receipt that
carries the operator's evidence, so the next tick dispatches them again. It refuses ``alert``
(use ``alerts``) and ``pool`` (the tick reconciles pool rows against the pool itself).

Neither subcommand archives mail or removes a label.

Both hold the state directory's writer lock (em_runtime.writer_lock, the one the tick holds for
its whole run) and refuse with exit code 3 while a tick holds it. Every row is written by
re-reading its state file, changing that one row and saving: a whole state copy loaded earlier is
never written back, so nothing another writer advanced in the meantime is reverted.
"""
import argparse
import contextlib
import copy
import datetime
import email.utils
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import em_actions  # noqa: E402
import em_alert    # noqa: E402
import em_runtime  # noqa: E402
import em_watch    # noqa: E402

LEVELS = {"紧急": "URGENT", "待办": "ACTION", "知悉": "FYI", "噪音": "NOISE"}
RANK = {"URGENT": 0, "ACTION": 1, "SUMMARY": 2, "FYI": 3, "NOISE": 4, "UNKNOWN": 5}
LABEL = {"URGENT": "紧急", "ACTION": "待办", "SUMMARY": "每日汇总", "FYI": "知悉", "NOISE": "噪音",
         "UNKNOWN": "未知"}
REQUEUE_REFUSED = {"alert": "alerts are delivered by the `alerts` catch-up, never resent one by one",
                   "pool": "pool rows are reconciled against the pool by every tick"}
JOURNAL = "catchup.state.json"


def level_of(message):
    text = (message or "").strip()
    if text.startswith("【") and "】" in text:
        return LEVELS.get(text[1:text.index("】")], "UNKNOWN")
    return "UNKNOWN"


def catchup_key(member_keys):
    return "catchup:" + hashlib.sha256("\n".join(sorted(member_keys)).encode()).hexdigest()


def catchup_receipt(member_key, adapter, key, relay_receipt, delivered_at):
    """A confirmed receipt for one member, proving delivery through the catch-up `key`."""
    return {"status": "confirmed", "idempotency_key": member_key, "adapter": adapter,
            "receipt_id": "%s/%s" % (key, relay_receipt["receipt_id"]),
            "delivery": "catch_up", "catchup_key": key, "delivered_at": delivered_at}


def collect(account_states, summary_state):
    """Every alert left `uncertain` as an entry: {'scope', 'key', 'adapter', 'message', ...}.

    `pending` rows are excluded: a running tick is about to send them itself.

    account_states maps an account slug to its loaded state.
    """
    entries = []
    for slug, state in sorted(account_states.items()):
        for key, row in sorted((state.get("actions") or {}).items()):
            if row.get("action") != "alert" or row.get("status") != "uncertain":
                continue
            entries.append({"scope": ("account", slug), "key": key, "adapter": "alert",
                            "account": row["account"], "message_id": row["message_id"],
                            "message": row["payload"].get("message", ""),
                            "level": level_of(row["payload"].get("message", ""))})
    for run_key, run in sorted(((summary_state or {}).get("summary_runs") or {}).items()):
        item_id = next((step["payload"].get("item_id") for step in run.get("steps", [])
                        if step.get("adapter") == "summary_mark_done"), None)
        for step in run.get("steps", []):
            if step.get("adapter") == "alert" and step.get("status") == "uncertain":
                entries.append({"scope": ("summary", run_key), "key": step["key"], "adapter": "alert",
                                "account": "", "message_id": "", "item_id": item_id,
                                "at": run.get("scheduled_at") or run.get("created_at") or "",
                                "message": step["payload"].get("message", ""), "level": "SUMMARY"})
    return entries


def _when(entry):
    parsed = None
    if entry.get("at"):  # ISO time: a daily summary's own scheduled (or created) time
        try:
            parsed = datetime.datetime.fromisoformat(str(entry["at"]).replace("Z", "+00:00"))
        except ValueError:
            parsed = None
    if parsed is None and entry.get("date"):
        try:
            parsed = email.utils.parsedate_to_datetime(entry["date"])
        except (TypeError, ValueError):
            parsed = None
    if parsed is not None and parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed


def order(entries):
    """Most important first; within a level, newest first; undated last."""
    def sort_key(entry):
        when = _when(entry)
        stamp = when.timestamp() if when is not None else float("-inf")
        return (RANK.get(entry["level"], RANK["UNKNOWN"]), -stamp if when is not None else float("inf"))
    return sorted(entries, key=sort_key)


def _sender(value):
    name, address = email.utils.parseaddr(value or "")
    domain = address.rsplit("@", 1)[-1] if "@" in address else ""
    name = em_alert.redact_push(name, limit=40) if name else ""
    if name and domain:
        return "%s (%s)" % (name, domain)
    return name or domain or "发件人未知"


def render(entries, now=None):
    """The consolidated catch-up text. Subjects pass the same redaction as normal alerts."""
    entries = order(entries)
    lines = ["补发:以下 %d 条邮件提醒当时没有送达(中继故障),按重要程度排列。" % len(entries)]
    for entry in entries:
        when = _when(entry)
        stamp = when.strftime("%m-%d %H:%M") if when is not None else "时间未知"
        head = "[%s] %s" % (LABEL.get(entry["level"], "未知"), stamp)
        if entry["level"] == "SUMMARY":
            lines.append("%s 当时未送达的每日汇总:\n%s" % (head, entry["message"]))
            continue
        subject = em_alert.redact_push(entry.get("subject", ""), limit=80)
        detail = " | ".join(part for part in (_sender(entry.get("from", "")), subject) if part)
        lines.append("%s %s\n  %s" % (head, entry["message"], detail))
    return "\n".join(lines)


def _all_mail(box):
    r"""The \All special-use mailbox; its name is localized (and modified UTF-7) per account."""
    typ, lines = box.list()
    for line in (lines or []) if typ == "OK" else []:
        if isinstance(line, bytes) and rb"\All" in line:
            parts = line.split(b'"')
            if len(parts) >= 2:
                return parts[-2].decode("latin-1")
    return "[Gmail]/All Mail"


def imap_lookup(user, password, message_ids):
    """Read-only header lookup by Message-ID in All Mail. Returns {message_id: headers}."""
    import imaplib
    from email.parser import BytesHeaderParser
    found = {}
    box = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        box.login(user, password)
        typ, _ = box.select('"%s"' % _all_mail(box), readonly=True)
        if typ != "OK":
            raise RuntimeError("cannot open All Mail read-only")
        for mid in message_ids:
            typ, data = box.uid("SEARCH", "X-GM-RAW", '"rfc822msgid:%s"' % mid.strip("<>"))
            uids = (data[0] or b"").split() if typ == "OK" and data else []
            if not uids:
                continue
            typ, fetched = box.uid("FETCH", uids[0], "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            for item in fetched or []:
                if isinstance(item, tuple):
                    headers = BytesHeaderParser().parsebytes(item[1])
                    found[mid] = {k: em_watch.dec(headers.get(k, "")) for k in ("from", "subject", "date")}
    finally:
        try:
            box.logout()
        except Exception:
            pass
    return found


def enrich(entries, lookup):
    """Attach date/from/subject from lookup(account, [message_ids]) -> {mid: headers}."""
    by_account = {}
    for entry in entries:
        if entry["message_id"]:
            by_account.setdefault(entry["account"], []).append(entry["message_id"])
    for account, mids in by_account.items():
        try:
            found = lookup(account, mids) or {}
        except Exception as error:  # a missing header must not block delivery of the alert itself
            sys.stderr.write("email-monitor catch-up: header lookup failed for one account: %s\n"
                             % type(error).__name__)
            found = {}
        for entry in entries:
            if entry["account"] == account and entry["message_id"] in found:
                entry.update(found[entry["message_id"]])
    return entries


def enrich_summaries(entries, list_items):
    """Give a summary without a recorded time its pool event's due (or created) time.

    Runs created before the summary worker recorded `scheduled_at` only name their pool item.
    list_items() -> pool items; a failed listing leaves the time unknown and blocks nothing.
    """
    wanted = [e for e in entries if e["level"] == "SUMMARY" and not e.get("at") and e.get("item_id")]
    if not wanted:
        return entries
    try:
        items = {str(item.get("id")): item for item in (list_items() or []) if isinstance(item, dict)}
    except Exception as error:
        sys.stderr.write("email-monitor catch-up: pool listing failed: %s\n" % type(error).__name__)
        return entries
    for entry in wanted:
        item = items.get(str(entry["item_id"])) or {}
        entry["at"] = item.get("due_at") or item.get("created_at") or ""
    return entries


def _before(entries, cutoff):
    """Keep account alerts whose message date is before `cutoff` (aware datetime); summaries stay.

    An uncertain row from after the cause was fixed may have been delivered, so it is not part
    of a catch-up; an undated row cannot be placed and is left alone too.
    """
    if cutoff is None:
        return entries
    kept = []
    for entry in entries:
        when = _when(entry)
        if entry["level"] == "SUMMARY" or (when is not None and when < cutoff):
            kept.append(entry)
    return kept


def run_alerts(account_states, summary_state, journal, send, lookup, dry=False, now=None, before=None,
               list_items=None):
    """Plan, journal, send once, then mark. Returns (report, account_states, summary_state, journal).

    Inputs are not mutated. The caller persists the journal, then the marks row by row from the
    journal (apply_marks); the returned state copies show the result and are never written back.
    """
    account_states = copy.deepcopy(account_states)
    summary_state = copy.deepcopy(summary_state)
    journal = copy.deepcopy(journal) if journal else {"catchups": {}}
    # Members of an earlier completed catch-up are marked, never sent again (a tick that was
    # running while the marks were saved can have written its older copy back).
    remarked = sum(mark(account_states, summary_state, done_key, done)
                   for done_key, done in journal["catchups"].items() if done["status"] == "completed")
    if any(r["status"] != "completed" for r in journal["catchups"].values()):
        raise RuntimeError("an earlier catch-up was interrupted after its journal entry; verify the "
                           "channel before resolving it by hand")
    entries = collect(account_states, summary_state)
    if entries:
        entries = _before(enrich_summaries(enrich(entries, lookup), list_items or (lambda: [])), before)
    if not entries:
        return ({"status": "nothing_pending", "members": 0, "remarked": remarked},
                account_states, summary_state, journal)
    key = catchup_key([e["key"] for e in entries])
    text = render(entries, now)
    if dry:
        return ({"status": "planned", "members": len(entries), "catchup_key": key, "text": text},
                account_states, summary_state, journal)
    members = sorted(e["key"] for e in entries)
    journal["catchups"][key] = {"status": "uncertain", "members": members, "receipt": None}
    receipt = send(text, key, copy.deepcopy(journal))
    disposition = em_actions.receipt_status(receipt, key, "alert")
    if disposition != "confirmed":
        if disposition == "not_applied":
            del journal["catchups"][key]  # proven unsent: nothing to reconcile
        return ({"status": "not_delivered", "members": len(entries), "catchup_key": key},
                account_states, summary_state, journal)
    delivered_at = (now or datetime.datetime.now(datetime.timezone.utc)).isoformat()
    journal["catchups"][key] = record = {"status": "completed", "members": members,
                                         "receipt": receipt, "delivered_at": delivered_at}
    marked = mark(account_states, summary_state, key, record)
    return ({"status": "completed", "members": len(entries), "marked": marked, "catchup_key": key,
             "relay_receipt": receipt["receipt_id"]}, account_states, summary_state, journal)


def _mark_account(state, members, key, record):
    marked = 0
    for row_key, row in (state.get("actions") or {}).items():
        if row_key in members and row["action"] == "alert" and row["status"] != "completed":
            row.update(status="completed", receipt=catchup_receipt(
                row_key, "alert", key, record["receipt"], record["delivered_at"]))
            marked += 1
    return marked


def _mark_summary(summary_state, members, key, record):
    marked = 0
    for run in ((summary_state or {}).get("summary_runs") or {}).values():
        for step in run.get("steps", []):
            if step["key"] in members and step["adapter"] == "alert" and step["status"] != "completed":
                step.update(status="completed", receipt=catchup_receipt(
                    step["key"], "alert", key, record["receipt"], record["delivered_at"]))
                marked += 1
    return marked


def mark(account_states, summary_state, key, record):
    """Mark every member of a completed catch-up delivered. Idempotent."""
    members = set(record["members"])
    return (sum(_mark_account(state, members, key, record) for state in account_states.values())
            + _mark_summary(summary_state, members, key, record))


def _rmw(path, change, load, save):
    """Re-read one state file, apply `change` to the fresh copy, save only if it changed something."""
    state = load(path)
    if change(state):
        save(path, state)
        return True
    return False


def apply_marks(journal, account_paths, summary_path, load=None, save=None):
    """Persist the marks of every completed catch-up, one row per re-read/modify/write.

    Only the member row is changed in a fresh copy, so a row another writer advanced since the
    catch-up loaded its states keeps that writer's value. Returns the number of rows marked.
    """
    load = load or em_watch.load_state
    save = save or em_watch.save_state
    marked = 0
    for key, record in sorted(((journal or {}).get("catchups") or {}).items()):
        if record.get("status") != "completed":
            continue
        for member in record["members"]:
            one = {member}
            done = any(_rmw(path, lambda state: _mark_account(state, one, key, record), load, save)
                       for path in account_paths)
            if not done:
                done = _rmw(summary_path, lambda state: _mark_summary(state, one, key, record), load, save)
            marked += bool(done)
    return marked


def _check_requeue(action, evidence):
    if action in REQUEUE_REFUSED:
        raise ValueError(REQUEUE_REFUSED[action])
    if action not in em_actions.ACTIONS:
        raise ValueError("unknown action: %s" % action)
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError("requeue needs the evidence that these rows were never applied")


def _requeue_row(state, key, action, evidence, statuses):
    row = (state.get("actions") or {}).get(key)
    if not isinstance(row, dict) or row.get("action") != action or row.get("status") not in statuses:
        return False
    row.update(status="failed", receipt={"status": "not_applied", "idempotency_key": key,
                                          "adapter": action, "evidence": evidence.strip()})
    return True


def requeue(state, action, evidence, statuses=("uncertain",)):
    """Mark `action` rows in `statuses` as never applied so the next tick dispatches them again."""
    _check_requeue(action, evidence)
    state = copy.deepcopy(state)
    count = sum(_requeue_row(state, key, action, evidence, statuses)
                for key in list((state.get("actions") or {})))
    return state, count


def requeue_rows(path, keys, action, evidence, statuses=("uncertain",), load=None, save=None):
    """requeue() persisted one row per re-read/modify/write; a row no longer in `statuses` is skipped."""
    _check_requeue(action, evidence)
    load = load or em_watch.load_state
    save = save or em_watch.save_state
    return sum(_rmw(path, lambda state, key=key: _requeue_row(state, key, action, evidence, statuses),
                    load, save)
               for key in keys)


def _cutoff(value):
    cutoff = datetime.datetime.fromisoformat(value)
    if cutoff.tzinfo is None:
        raise ValueError("--before needs a UTC offset")
    return cutoff


def _load_config(path):
    with open(path, encoding="utf-8-sig") as handle:
        cfg = json.load(handle)
    companion = os.path.dirname(os.path.abspath(path))
    storage, _ = em_runtime.storage_config(cfg, companion)
    return cfg, storage


def main(argv=None):
    import em_tick
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", required=True)
    ap.add_argument("--reminder", default=None, help="reminder.py, for daily-summary times (read only)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_alerts = sub.add_parser("alerts")
    p_alerts.add_argument("--resolve-cred")
    p_alerts.add_argument("--dry", action="store_true")
    p_alerts.add_argument("--before", required=True,
                          help="ISO time the cause was fixed; only alerts for mail older than this are caught up")
    p_requeue = sub.add_parser("requeue")
    p_requeue.add_argument("--action", required=True)
    p_requeue.add_argument("--evidence", required=True)
    a = ap.parse_args(argv)
    cfg, storage = _load_config(a.config)
    with contextlib.ExitStack() as writer:
        if not (a.cmd == "alerts" and a.dry):
            try:
                writer.enter_context(em_runtime.writer_lock(storage["state_dir"], "catchup"))
            except em_runtime.WriterBusy as busy:
                sys.stderr.write("email-monitor catch-up refused: %s\n" % busy)
                print(json.dumps({"status": "refused", "error": str(busy)}, ensure_ascii=False))
                return 3
        return _locked_main(a, cfg, storage, em_tick)


def _locked_main(a, cfg, storage, em_tick):
    accounts = [acct for acct in cfg.get("accounts", []) if isinstance(acct, dict)]
    paths = {acct["slug"]: os.path.join(storage["state_dir"], "%s.state.json" % acct["slug"])
             for acct in accounts if em_runtime.valid_account_slug(acct.get("slug", ""))}
    states = {acct["slug"]: em_actions.load_state(em_watch.load_state(paths[acct["slug"]]), acct["user"])
              for acct in accounts if acct.get("slug") in paths}
    if a.cmd == "requeue":
        _check_requeue(a.action, a.evidence)
        report = {}
        for slug, state in states.items():
            keys = sorted(key for key, row in state["actions"].items()
                          if row["action"] == a.action and row["status"] == "uncertain")
            report[slug] = requeue_rows(paths[slug], keys, a.action, a.evidence)
        print(json.dumps({"status": "completed", "requeued": report}, ensure_ascii=False))
        return 0
    summary_path = os.path.join(storage["state_dir"], "summary.state.json")
    journal_path = os.path.join(storage["state_dir"], JOURNAL)
    summary_state = em_watch.load_state(summary_path)
    journal = em_watch.load_state(journal_path) if os.path.isfile(journal_path) else None
    users = {acct["user"].strip().lower(): acct for acct in accounts}

    def lookup(account, mids):
        acct = users[account]
        password = em_tick.resolve_app_pw(a.resolve_cred, acct.get("cred_path", ""))
        return imap_lookup(account, password, mids)

    def list_items():
        import em_pool
        return em_pool.list_items(a.reminder or em_pool.default_reminder_path(), storage["db"])

    def send(text, key, journal_snapshot):
        em_watch.save_state(journal_path, journal_snapshot)  # intent before the effect
        return em_alert.send(text, idempotency_key=key)

    report, _, _, journal2 = run_alerts(states, summary_state, journal, send, lookup, dry=a.dry,
                                        before=_cutoff(a.before), list_items=list_items)
    if not a.dry:
        if journal2 != (journal or {"catchups": {}}):
            em_watch.save_state(journal_path, journal2)
        if report["status"] in ("completed", "nothing_pending"):
            # Row by row on fresh copies, never the copies loaded above (see apply_marks).
            report["persisted"] = apply_marks(journal2, [paths[slug] for slug in sorted(states)], summary_path)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] in ("completed", "planned", "nothing_pending") else 1


if __name__ == "__main__":
    sys.exit(main())
