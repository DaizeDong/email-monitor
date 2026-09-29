#!/usr/bin/env python3
"""Email-monitor heartbeat: plan, persist intent, dispatch, verify acknowledgement.

The account ledger distinguishes observation cursors from alert, pool, archive and
label completion. Dry execution prints a plan without persistent writes. Runtime
DATA belongs to a verified PRIVATE Git companion. See reference/delivery-state.md
for reconciliation limits and the separate optional daily-summary workflow.
"""
import argparse
import datetime
import copy
import contextvars
from pathlib import Path
import json
import os
import re
import subprocess
import sys

# On Windows, child console apps (powershell, python) flash a console window even
# when the parent runs under pythonw. CREATE_NO_WINDOW keeps every tick invisible.
_NOWINDOW = {"creationflags": 0x08000000} if sys.platform == "win32" else {}

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import em_classify        # noqa: E402
import em_agent_classify
import em_pool            # noqa: E402
import em_alert           # noqa: E402
import em_watch           # noqa: E402
import em_dates           # noqa: E402
import em_topic            # noqa: E402
import em_retry            # noqa: E402
import em_actions
import em_runtime
# Module import (not `from llmcall import call`): the transport closure below reaches
# llmcall.call at call time, so tests can monkeypatch em_tick.llmcall.call directly.
try:
    import llmcall  # noqa: E402
except ImportError:
    llmcall = None

LOG = os.path.expanduser(os.environ.get(
    "EMAIL_MONITOR_LOG", "~/.local/state/email-monitor/email-monitor.log"))
LABEL_TOOL = os.path.expanduser(os.environ.get(
    "EMAIL_MONITOR_LABEL_TOOL", "~/.local/bin/gmail-imap-label.py"))

ENV_VAR = "EMAIL_MONITOR_CONFIG"
_DRY = contextvars.ContextVar("email_monitor_dry", default=False)
_LOG = contextvars.ContextVar("email_monitor_log", default=None)


def resolve_config(explicit):
    """Locate registry.json (config-spec E2). Explicit --config wins; else discovery dir order:
    $EMAIL_MONITOR_CONFIG -> $EMAIL_MONITOR_CONFIG_DIR -> ~/.email-monitor-config/ ->
    ~/.config/email-monitor-config/, then <dir>/registry.json. Returns a path or None (no crash)."""
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    for v in (ENV_VAR, ENV_VAR + "_DIR"):
        val = os.environ.get(v)
        if val:
            return os.path.join(os.path.abspath(os.path.expanduser(val)), "registry.json")
    for d in (os.path.expanduser("~/.email-monitor-config"),
              os.path.expanduser("~/.config/email-monitor-config")):
        if os.path.isdir(d):
            return os.path.join(d, "registry.json")
    return None


def log(msg):
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    line = "[%s] %s" % (ts, msg)
    if not _DRY.get():
        destination = _LOG.get() or LOG
        em_runtime.prove_private(destination)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        with open(destination, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    print(line)


def preflight(reminder, resolve_cred):
    # NOTE: reminder.py (the schedule-reminder base) is intentionally NOT hard-required here. Without
    # it email-monitor still watches, classifies and alerts; it only skips pool + dated-reminder
    # tracking (gated by pool_enabled in main). So its absence is a soft, non-fatal mode switch, not a
    # preflight failure. The hard requirements are the alert egress + label tool + credential resolver.
    missing = []
    for path, label in [(LABEL_TOOL, "gmail-imap-label.py"), (em_alert.RELAY, "discord relay")]:
        if not os.path.isfile(path):
            missing.append(label)
    if resolve_cred and not os.path.isfile(resolve_cred):
        missing.append("resolve-cred.ps1")
    return missing


def resolve_app_pw(resolve_cred, cred_path):
    """Decrypt a DPAPI .cred via the config repo's resolve-cred.ps1. Returns pw, never logs it."""
    if not resolve_cred:
        return os.environ.get("GMAIL_APP_PW")  # test path
    p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", resolve_cred, "-CredPath", os.path.expanduser(cred_path)],
                       capture_output=True, text=True, encoding="utf-8", **_NOWINDOW)
    if p.returncode != 0:
        raise RuntimeError("resolve-cred failed (rc=%d)" % p.returncode)
    return (p.stdout or "").strip()


def _mail_effect(user, rfc_msgid, label, dry, action, app_pw=None,
                 idempotency_key=None, python=None):
    """Preserve matching delivery or non-delivery proof from the label helper."""
    if not rfc_msgid or dry:
        return False if idempotency_key is None else {"status": "uncertain"}
    mid = str(rfc_msgid).strip().strip("<>")
    args = [python or sys.executable, LABEL_TOOL, "--user", user, "--query",
            "rfc822msgid:%s" % mid, "--add", label]
    if action == "archive":
        args.append("--archive")
    if idempotency_key:
        args += ["--idempotency-key", idempotency_key, "--json"]
    env = dict(os.environ)
    if app_pw:
        env["GMAIL_APP_PW"] = app_pw
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                            env=env, **_NOWINDOW)
    receipt = None
    try:
        receipt = json.loads((result.stdout or "").strip())
    except (ValueError, TypeError):
        pass
    key = idempotency_key or (receipt.get("idempotency_key") if isinstance(receipt, dict) else None)
    disposition = "uncertain"
    if isinstance(key, str) and key:
        disposition = em_actions.receipt_status(receipt, key, action)
    valid = bool(result.returncode == 0 and isinstance(receipt, dict)
                 and disposition == "confirmed"
                 and type(receipt.get("matched")) is int and receipt["matched"] > 0
                 and receipt.get("applied") is True)
    if idempotency_key is None:
        return valid
    if valid or disposition == "not_applied":
        return receipt
    return {"status": "uncertain", "idempotency_key": idempotency_key, "adapter": action}


def archive(user, rfc_msgid, label, dry, app_pw=None, idempotency_key=None, python=None):
    """Label and archive a message; process exit alone is not confirmation."""
    return _mail_effect(user, rfc_msgid, label, dry, "archive", app_pw, idempotency_key, python)


# The topic verdict's wire format. Validated by the transport, not by em_topic.judge, so a
# malformed reply costs a same-provider retry instead of an abstention.
TOPIC_SCHEMA = {
    "type": "object",
    "properties": {"labels": {"type": "array", "items": {
        "type": "object",
        "properties": {"label": {"type": "string"}, "evidence": {"type": "string"}},
        "required": ["label", "evidence"]}}},
    "required": ["labels"],
}


def _make_transport(timeout=None, log=None):
    """Adapt llmcall to the contract em_topic.judge expects.

    llmcall never raises; it returns a falsy Result when the whole provider chain failed.
    Converting that to None is what lets judge report `failed` (an outage) rather than
    `unsure` (a taxonomy problem) -- those two states exist precisely to be told apart.
    mode="judge" is the right tier and already the default: read only, no MCP, deterministic.
    """
    em_runtime.reject_model_overrides(timeout=timeout)
    def _call(prompt):
        r = llmcall.call(prompt, schema=TOPIC_SCHEMA, mode="judge", log=log)
        return r.data if r else None
    return _call


def _label_add(user, rfc_msgid, label, dry, app_pw=None, idempotency_key=None, python=None):
    """Add a topic label without removing the message from its mailbox."""
    return _mail_effect(user, rfc_msgid, label, dry, "topic_label", app_pw, idempotency_key, python)


def topic_label(user, slug, records, dry, app_pw=None, timeout=None, state=None):
    """Compatibility planner; dry execution never writes or mutates caller state."""
    em_runtime.reject_model_overrides(timeout=timeout)
    token = _DRY.set(bool(dry))
    try:
        return _topic_label(user, slug, records, dry, app_pw, timeout,
                            copy.deepcopy(state) if dry else state)
    finally:
        _DRY.reset(token)


def _topic_label(user, slug, records, dry, app_pw=None, timeout=None, state=None):
    """Gated topic-labeling step: decide what each new message is about, and add the
    decided labels. Never removes \\Inbox -- see `_label_add`, its only writer.

    Loads the private per-account config once per tick (`em_topic.load_config`) and
    returns immediately when it is None: an uninitialised machine stays inert rather
    than erroring, the same posture as the rest of this file's optional co-ops. This
    function is only reached when topic_labeling.enabled is True (see caller), so a
    None config here means "on but uninitialised", not "off" -- and the top level
    enabled= log line cannot tell those apart on its own, so this logs it explicitly.

    When `state` is given, messages whose verdict came back `failed` are queued in it
    and retried on later ticks (see em_retry). `failed` means the model was never
    reached, so the message has not been judged at all; dropping it would lose it for
    good, because the caller advances the INBOX cursor whatever happened here. Retries
    run BEFORE the fresh batch so an outage drains in arrival order, and they only ever
    reach this function -- never the alert, archive or pool steps, which already ran for
    those messages on their first pass and must not run twice.
    """
    em_runtime.reject_model_overrides(timeout=timeout)
    cfg = em_topic.load_config(slug, log=log)
    if cfg is None:
        log("ACCOUNT %s: topic_labeling enabled but not configured (no private "
            "taxonomy for this account) -- no labels added" % slug)
        return 0
    call = _make_transport(log=log)
    n_labeled = 0
    # Counting only successes makes the one question worth asking unanswerable.
    # `topic_labeled=N` cannot distinguish "the gate is calibrated and most mail
    # genuinely has no label" from "the gate refuses everything" from "the model
    # chain is down", yet those need three different responses. unsure is a
    # taxonomy signal, failed is an outage, and both write nothing -- so both are
    # invisible unless they are counted here.
    states = {"decided": 0, "unsure": 0, "failed": 0}

    queue = em_retry.load(state) if state is not None else []
    retried = [em_retry.to_record(e) for e in queue]
    kept = []          # entries still owed a future attempt
    seen_retry = set()

    for r in retried + list(records):
        mid = r.get("message_id", "")
        is_retry = mid in {e.get("message_id") for e in queue} and mid not in seen_retry
        if is_retry:
            seen_retry.add(mid)
        msg = {"from": r.get("from", ""), "subject": r.get("subject", ""),
               "date": r.get("date", ""), "list_id": r.get("list_id", "")}
        verdict = em_topic.judge(msg, cfg["taxonomy"], cfg["sender_map"],
                                 cfg["allowed_labels"], call=call, log=log,
                                 type_labels=cfg["type_labels"])
        state_name = verdict["state"]
        states[state_name] = states.get(state_name, 0) + 1

        if state_name == "failed" and state is not None:
            entry = next((e for e in queue if e.get("message_id") == mid), None)
            if entry is None:
                queue = em_retry.enqueue(queue, r, verdict.get("reason", ""), log=log)
            else:
                # Already waiting: count this attempt, and give up once the cap is hit
                # rather than letting one message be retried forever.
                if em_retry.mark_attempt(entry):
                    kept.append(entry)
                else:
                    log("ACCOUNT %s: topic retry giving up after %d attempts msgid=%s subject=%r"
                        % (slug, entry.get("attempts"), mid, r.get("subject", "")[:60]))
            continue

        if state_name != "decided":
            continue
        for item in verdict["labels"]:
            if dry or _label_add(user, r.get("message_id"), item["label"], False, app_pw=app_pw):
                n_labeled += 1

    if state is not None:
        # Anything that just succeeded (decided or unsure -- both are real verdicts)
        # leaves the queue. Only entries re-queued above survive, plus newly failed ones.
        fresh_failures = [e for e in queue
                          if e.get("message_id") not in seen_retry
                          and not em_retry.exhausted(e)]
        em_retry.store(state, kept + fresh_failures)

    if records or retried:
        log("ACCOUNT %s: topic verdicts judged=%d (retried=%d) decided=%d unsure=%d "
            "failed=%d labels_added=%d retry_queue=%d"
            % (slug, len(records) + len(retried), len(retried), states["decided"],
               states["unsure"], states["failed"],
               n_labeled, len(em_retry.load(state)) if state is not None else 0))
    return n_labeled


def classify_record(msg, rules, agent_cfg):
    """Judge using installed llmcall policy, with deterministic heuristic fallback."""
    em_runtime.check_model_settings({"classifier": agent_cfg})
    if agent_cfg.get("mode", "agent") == "agent":
        cls = em_agent_classify.classify(
            msg,
            owner=agent_cfg.get("owner", ""),
            log=log)
        if cls is not None:
            return cls
        log("ACCOUNT %s: all agent providers failed -> heuristic fallback"
            % msg.get("account", "?"))
    return em_classify.classify(msg, rules)


def classify_records_parallel(msgs, rules, agent_cfg):
    """Classify a list of messages CONCURRENTLY, returning verdicts in the SAME ORDER as msgs. Each
    message's classification is independent (agent judgment + heuristic fallback, no shared state), so
    N new mails in a tick run one provider call each at the same time instead of back-to-back. Falls
    back to the serial path for 0/1 messages or when the agent chain is disabled (nothing to overlap).
    A per-message failure degrades to the heuristic verdict, never taking the whole tick down."""
    em_runtime.check_model_settings({"classifier": agent_cfg})
    if not msgs:
        return []
    max_workers = int(agent_cfg.get("max_parallel", 8)) if agent_cfg else 8
    # Only the agent path makes a (slow, IO-bound) provider call worth overlapping; the pure-heuristic
    # path is CPU-only microseconds, so run it serially and skip the thread pool overhead.
    agent_on = bool(agent_cfg) and agent_cfg.get("mode", "agent") == "agent"
    if len(msgs) == 1 or max_workers <= 1 or not agent_on:
        return [classify_record(m, rules, agent_cfg) for m in msgs]

    import concurrent.futures as _cf

    def _one(m):
        try:
            return classify_record(m, rules, agent_cfg)
        except Exception as e:  # never let one message's failure sink the tick
            log("ACCOUNT %s: classify failed (%s) -> heuristic" % (m.get("account", "?"), str(e)[:100]))
            return em_classify.classify(m, rules)

    with _cf.ThreadPoolExecutor(max_workers=min(max_workers, len(msgs))) as ex:
        contexts = [(contextvars.copy_context(), message) for message in msgs]
        return list(ex.map(lambda pair: pair[0].run(_one, pair[1]), contexts))


def reconcile_action(action_record):
    """Unknown until a downstream adapter can prove this key's prior disposition.

    Operators may supply a reconciliation adapter with matching confirmed or
    not_applied receipts. Legacy relay/label helpers expose no such query, so the
    default never authorizes a blind resend after an interrupted delivery.
    """
    return {"status": "uncertain", "idempotency_key": action_record["idempotency_key"],
            "adapter": action_record["action"]}


def _plan_record(acct, mailbox, generation, record, verdict, rules, pool_enabled,
                 archive_enabled):
    user = acct["user"].strip().lower()
    slug = acct.get("slug", user.split("@")[0])
    priority, semantic = verdict["priority"], verdict["label"]
    label = acct.get("label_scheme", "EM/{priority}/{semantic}").replace(
        "{priority}", priority).replace("{semantic}", semantic)
    plans = []
    def add(action, payload):
        plans.append(em_actions.new_action(user, mailbox, generation, record, action, payload))
    if priority in set(rules.get("discord_push_levels", ["URGENT", "ACTION"])):
        add("alert", {"message": em_alert.build_title(priority, slug, record.get("subject", ""),
             summary=verdict.get("summary_zh", ""), account_label=acct.get("display_zh"))})
    if pool_enabled and priority in ("URGENT", "ACTION", "FYI"):
        add("pool", {"thread_key": record.get("thread_key", record["message_id"]),
            "title": derive_title(priority, semantic, record.get("subject", ""), verdict.get("summary_zh", "")),
            "kind": "task" if priority in ("URGENT", "ACTION") else "event",
            "due_at": verdict.get("due_at") or em_dates.normalize_due_at(verdict.get("due_raw"), base=record.get("date")),
            "priority": 2 if priority == "URGENT" else 4 if priority == "ACTION" else 7,
            "tags": ["acct:%s" % slug, semantic],
            "ext_extra": {"account": user, "uid": record.get("uid"), "subject_raw": record.get("subject", ""),
                          "from": record.get("from", ""), "label": label, "priority_tier": verdict.get("tier")}})
    if priority == "NOISE" and archive_enabled:
        add("archive", {"label": label})
    return plans


def _plan_topics(state, acct, records, timeout, enabled, config_dir=None):
    """Retain unjudged legacy headers; a cursor is never proof of a topic verdict."""
    pending = list(state["topic_retry"])
    if enabled:
        known = {(r.get("mailbox", "INBOX"), r.get("uidvalidity"), r["message_id"]) for r in pending}
        for record in records:
            key = (record["mailbox"], record["uidvalidity"], record["message_id"])
            if key not in known:
                pending.append({key: record.get(key, "") for key in (
                    "from", "subject", "date", "list_id", "mailbox", "uidvalidity", "message_id")})
                known.add(key)
    if not pending or not enabled:
        return
    options = {"config_dir": config_dir} if config_dir else {}
    cfg = em_topic.load_config(acct.get("slug", acct["user"].split("@")[0]), log=log, **options)
    if cfg is None:
        state["topic_retry"] = pending
        return
    em_runtime.reject_model_overrides(timeout=timeout)
    call = _make_transport(log=log)
    kept = []
    for record in pending:
        mailbox = record.get("mailbox", "INBOX")
        generation = record.get("uidvalidity")
        if generation is None:
            generation = state["cursors"].get(acct["user"].strip().lower() + "::" + mailbox, {}).get("uidvalidity")
        if generation is None:
            kept.append(record)
            continue
        message = {key: record.get(key, "") for key in ("from", "subject", "date", "list_id")}
        try:
            verdict = em_topic.judge(message, cfg["taxonomy"], cfg["sender_map"],
                                    cfg["allowed_labels"], call=call, log=log,
                                    type_labels=cfg.get("type_labels", []))
            if verdict.get("state") not in ("decided", "unsure"):
                kept.append(record)
                continue
            for item in verdict.get("labels", []) if verdict["state"] == "decided" else []:
                row = em_actions.new_action(acct["user"], mailbox, generation, record,
                                             "topic_label", {"label": item["label"]})
                state["actions"].setdefault(row["idempotency_key"], row)
        except Exception:
            kept.append(record)
    state["topic_retry"] = kept


def _dispatch(row, reminder, db, password, python):
    key, payload = row["idempotency_key"], row["payload"]
    if row["action"] == "alert":
        return em_alert.send(payload["message"], idempotency_key=key, python=python)
    if row["action"] == "pool":
        return em_pool.upsert(reminder, db, row["message_id"], idempotency_key=key,
                              python=python, **payload)
    adapter = archive if row["action"] == "archive" else _label_add
    return adapter(row["account"], row["message_id"], payload["label"], False,
                   app_pw=password, idempotency_key=key, python=python)


def process_account(acct, rules, reminder, db, resolve_cred, state_dir, dry, agent_cfg=None,
                    archive_enabled=True, pool_enabled=True, topic_enabled=False,
                    topic_timeout=None, runtime=None, log_path=None):
    """Plan, checkpoint intent, then dispatch only work with known disposition."""
    token = _DRY.set(bool(dry))
    log_token = _LOG.set(log_path or _LOG.get())
    result = {"account": acct.get("slug", ""), "status": "failed", "new": 0,
              "alert": 0, "archived": 0, "kept": 0, "topic_labeled": 0}
    if dry:
        result["planned_actions"] = []
    possible_effect = False
    try:
        agent_cfg = agent_cfg or {}
        runtime = runtime or {}
        em_runtime.check_model_settings({"classifier": agent_cfg})
        em_runtime.reject_model_overrides(topic_timeout=topic_timeout)
        em_runtime.check_local_route(runtime, agent_cfg, topic_enabled)
        user = acct["user"].strip().lower()
        slug = acct.get("slug", user.split("@")[0])
        if not em_runtime.valid_account_slug(slug):
            raise ValueError("invalid account slug")
        state_path = os.path.join(state_dir, "%s.state.json" % slug)
        for path in (state_path, db, log_path or _LOG.get() or LOG):
            if path:
                em_runtime.prove_private(path)
        state = em_actions.load_state(em_watch.load_state(state_path), user)
        folders = acct.get("monitored_folders", ["INBOX"])
        if not isinstance(folders, list) or not folders or any(not isinstance(f, str) or not f.strip() for f in folders):
            raise ValueError("monitored_folders must be a nonempty list of mailbox names")
        password = resolve_app_pw(resolve_cred, acct.get("cred_path", ""))
        if not password:
            raise ValueError("no app password resolved")
        fresh = []
        observed = set(state["observed_messages"])
        for folder in dict.fromkeys("INBOX" if f.upper() == "INBOX" else f for f in folders):
            cursor_key = user + "::" + folder
            cursor = copy.deepcopy(state["cursors"].get(cursor_key, {"uidvalidity": None, "last_uid": 0}))
            records, new_cursor = em_watch.run_once(user, folder, cursor, acct.get("max_batch", 400), app_pw=password)
            generation = new_cursor.get("uidvalidity")
            for record in records:
                record = copy.deepcopy(record)
                mid = record.get("message_id", "")
                if not isinstance(mid, str) or not mid.strip() or generation is None:
                    raise ValueError("fetched message has no durable message identity")
                identity = em_actions.identity(user, folder, generation, mid)
                if identity in observed:
                    continue
                observed.add(identity)
                record.update(mailbox=folder, uidvalidity=generation)
                fresh.append(record)
            state["cursors"][cursor_key] = copy.deepcopy(new_cursor)
        messages = [{"from": r.get("from", ""), "subject": r.get("subject", ""), "account": slug,
                     "list_unsubscribe": r.get("list_unsubscribe", False), "body": r.get("body", "")} for r in fresh]
        # Context variables do not automatically cross thread-pool boundaries.
        effective_agent = {**agent_cfg, "max_parallel": 1} if dry else agent_cfg
        verdicts = classify_records_parallel(messages, rules, effective_agent)
        if len(verdicts) != len(fresh):
            raise ValueError("classifier omitted a required message verdict")
        for record, verdict in zip(fresh, verdicts):
            for row in _plan_record(acct, record["mailbox"], record["uidvalidity"], record,
                                    verdict, rules, pool_enabled, archive_enabled):
                state["actions"].setdefault(row["idempotency_key"], row)
            if verdict["priority"] == "NOISE" and not archive_enabled:
                result["kept"] += 1
        state["observed_messages"] = sorted(observed)
        _plan_topics(state, acct, fresh, topic_timeout, topic_enabled, runtime.get("config_dir"))
        result["new"] = len(fresh)
        if dry:
            result.update(status="planned", planned_actions=copy.deepcopy([
                row for row in state["actions"].values() if row["status"] != "completed"]))
            result["pending_topics"] = len(state["topic_retry"])
            return result
        # Cursor and durable intents are committed before the first effect.
        em_watch.save_state(state_path, state)
        for row in state["actions"].values():
            if row["status"] == "completed":
                continue
            key, action = row["idempotency_key"], row["action"]
            if row["status"] == "uncertain":
                try:
                    receipt = reconcile_action(copy.deepcopy(row))
                except Exception:
                    receipt = None
                disposition = em_actions.receipt_status(receipt, key, action)
                if disposition == "confirmed":
                    row.update(status="completed", receipt=copy.deepcopy(receipt))
                    em_watch.save_state(state_path, state)
                    continue
                if disposition != "not_applied":
                    continue
                row.update(status="failed", receipt=copy.deepcopy(receipt))
            # A failed save here must stop dispatch. The last persisted status
            # remains authoritative, including after a crash during the adapter.
            row.update(status="uncertain", receipt=None)
            em_watch.save_state(state_path, state)
            possible_effect = True
            try:
                receipt = _dispatch(copy.deepcopy(row), reminder, db, password, runtime.get("python"))
            except Exception:
                receipt = None
            disposition = em_actions.receipt_status(receipt, key, action)
            if disposition == "confirmed":
                row.update(status="completed", receipt=copy.deepcopy(receipt))
            elif disposition == "not_applied":
                row.update(status="failed", receipt=copy.deepcopy(receipt))
            em_watch.save_state(state_path, state)
            if row["status"] == "completed":
                counter = {"alert": "alert", "archive": "archived", "topic_label": "topic_labeled"}.get(action)
                if counter:
                    result[counter] += 1
        pending = sum(row["status"] != "completed" for row in state["actions"].values()) + len(state["topic_retry"])
        result.update(status="incomplete" if pending else "completed", pending=pending)
        return result
    except Exception as error:
        # Do not log failure text to an unverified output or expose credentials.
        result.update(status="incomplete" if possible_effect else "failed", error=type(error).__name__ + ": " + str(error))
        return result
    finally:
        if dry:
            print(json.dumps(result, ensure_ascii=False))
        _DRY.reset(token)
        _LOG.reset(log_token)


def derive_title(priority, label, subject, summary=""):
    """The pool item's one-liner, in Chinese — this is what the daily summary lists.

    Prefers the classifier's Chinese gist (`summary_zh`); falls back to the redacted subject when
    no agent verdict was available. The old version forced ASCII, which erased Chinese subjects
    entirely and produced useless rows like "Review mail re new mail".
    """
    gist = em_alert.redact_push(summary) if (summary or "").strip() else \
        em_alert.redact_subject(subject, max_words=8)
    verb = "需回复" if priority in ("URGENT", "ACTION") else "待查看"
    return ("%s:%s" % (verb, gist or "邮件"))[:120]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--python", help="Override the configured interpreter for runtime checks and helpers")
    ap.add_argument("--rules")
    ap.add_argument("--db")
    ap.add_argument("--reminder", default=em_pool.default_reminder_path())
    ap.add_argument("--resolve-cred")
    ap.add_argument("--state-dir")
    ap.add_argument("--summary", default=os.path.join(HERE, "em_summary.py"))
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    report = {"results": [], "status": "failed"}
    dry_token = _DRY.set(a.dry)
    log_token = None
    try:
        config_path = resolve_config(a.config)
        if not config_path or not os.path.isfile(config_path):
            raise ValueError("no config found; initialize a PRIVATE companion and supply --config")
        with open(config_path, encoding="utf-8-sig") as handle:
            cfg = json.load(handle)
        if not isinstance(cfg, dict) or cfg.get("schema_version", 1) != 1:
            raise ValueError("registry schema_version must be 1")
        companion = os.path.dirname(config_path)
        runtime = em_runtime.runtime_config(cfg, companion)
        if a.python is not None:
            runtime["python"] = em_runtime.resolve_path(a.python, companion)
        runtime["config_dir"] = companion
        agent_cfg = cfg.get("classifier", {})
        topic_enabled = bool((cfg.get("topic_labeling", {}) or {}).get("enabled", False))
        em_runtime.check_local_route(runtime, agent_cfg, topic_enabled)
        if (runtime.get("local_only") and cfg.get("daily_summary", {}).get("enabled", False)
                and Path(a.summary).resolve() != Path(HERE, "em_summary.py").resolve()):
            raise ValueError("local_only cannot verify a custom summary worker's model transport")
        from em_lint_rules import draft_config
        draft_config(cfg.get("draft"))
        storage, proofs = em_runtime.storage_config(cfg, companion, a.state_dir, a.db)
        em_runtime.prove_private(config_path)
        ready, detail = em_runtime.probe_interpreter(runtime["python"])
        if not ready:
            raise ValueError(detail)
        missing = preflight(a.reminder, a.resolve_cred)
        if missing:
            raise ValueError("preflight missing: " + ", ".join(missing))
        log_token = _LOG.set(storage["log"])
        rules = {}
        rules_path = a.rules or os.path.join(companion, "rules", "merged.json")
        if os.path.isfile(rules_path):
            with open(rules_path, encoding="utf-8-sig") as handle:
                rules = json.load(handle)
        accounts = cfg.get("accounts")
        if not isinstance(accounts, list) or not accounts or any(not isinstance(acct, dict) for acct in accounts):
            raise ValueError("accounts must be a nonempty list")
        pool_enabled = em_pool.available(a.reminder)
        for acct in accounts:
            report["results"].append(process_account(
                acct, rules, a.reminder, storage["db"], a.resolve_cred, storage["state_dir"],
                a.dry, agent_cfg, archive_enabled=bool(cfg.get("archive", {}).get("enabled", True)),
                pool_enabled=pool_enabled, topic_enabled=topic_enabled,
                runtime=runtime, log_path=storage["log"]))
        statuses = {result["status"] for result in report["results"]}
        report["status"] = "failed" if "failed" in statuses else "incomplete" if "incomplete" in statuses else "planned" if a.dry else "completed"
        report["storage_checks"] = proofs
        if cfg.get("daily_summary", {}).get("enabled", False) and pool_enabled:
            if a.dry:
                report["daily_summary"] = {"status": "planned", "delivery": "not_measured"}
            else:
                worker = subprocess.run(
                    [runtime["python"], a.summary, "--config", config_path,
                     "--reminder", a.reminder, "--db", storage["db"]],
                    capture_output=True, text=True, encoding="utf-8", timeout=180, **_NOWINDOW)
                try:
                    summary = json.loads((worker.stdout or "").strip().splitlines()[-1])
                except (ValueError, IndexError):
                    summary = {"status": "incomplete", "error": "summary worker provided no structured result"}
                if not isinstance(summary, dict):
                    summary = {"status": "incomplete", "error": "invalid summary worker result"}
                report["daily_summary"] = summary
                if worker.returncode != 0 or summary.get("status") != "completed":
                    report["status"] = "incomplete"
    except Exception as error:
        report["status"] = "incomplete" if report["results"] else "failed"
        report["error"] = type(error).__name__ + ": " + str(error)
    finally:
        if log_token is not None:
            _LOG.reset(log_token)
        _DRY.reset(dry_token)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] in ("planned", "completed") else 1


if __name__ == "__main__":
    sys.exit(main())
