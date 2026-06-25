#!/usr/bin/env python3
"""email-monitor heartbeat tick — the monitoring spine (one OS task runs this every PT2-5M).

Per tick:
  (a) preflight: verify pythonw / relay / reminder.py / resolve-cred exist; else loud Discord alert
      + exit (avoids the schtasks minimal-PATH silent half-run, anti-pattern #12).
  (b) per account (strict isolation; one account failing never stops the others):
        resolve app pw via DPAPI -> env GMAIL_APP_PW -> em_watch (incremental, read-only) ->
        classify each new record -> URGENT/ACTION: redacted Discord alert + upsert into base pool ->
        NOISE: archive (label + de-inbox) -> FYI: upsert (no alert).
  (c) check base `due --source email-monitor` for the daily-summary event; if due, run em_summary.

This is the orchestrator. It NEVER auto-sends mail and NEVER prints secrets. Reads config from the
companion config repo's registry.json + merged rules. Logs to ~/.local/state/email-monitor/email-monitor.log.

Usage:
  python em_tick.py --config <registry.json> [--db PATH] [--reminder PATH] [--once] [--dry]
Stdlib only.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import em_classify  # noqa: E402
import em_pool      # noqa: E402
import em_alert     # noqa: E402
import em_watch     # noqa: E402

LOG = os.path.expanduser(os.path.join("~", ".claude", "logs", "email-monitor.log"))
LABEL_TOOL = os.path.expanduser(os.path.join("~", ".claude", "scripts", "gmail-imap-label.py"))


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    line = "[%s] %s" % (ts, msg)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)


def preflight(reminder, resolve_cred):
    missing = []
    for path, label in [(reminder, "reminder.py"), (LABEL_TOOL, "gmail-imap-label.py"),
                        (em_alert.RELAY, "discord relay")]:
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
                       capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        raise RuntimeError("resolve-cred failed (rc=%d)" % p.returncode)
    return (p.stdout or "").strip()


def archive(user, gm_msgid, label, dry):
    """Archive via the existing bulk tool: add label + de-inbox, selected by X-GM-MSGID."""
    if not gm_msgid:
        return
    query = "rfc822msgid:%s" % gm_msgid  # gmail search by msgid; precise single-message select
    args = [sys.executable, LABEL_TOOL, "--user", user, "--query", query,
            "--add", label, "--archive"]
    if dry:
        args.append("--dry")
    subprocess.run(args, capture_output=True, text=True, encoding="utf-8")


def process_account(acct, rules, reminder, db, resolve_cred, state_dir, dry):
    user = acct["user"]
    slug = acct.get("slug", user.split("@")[0])
    state_path = os.path.join(state_dir, "%s.state.json" % slug)
    label_scheme = acct.get("label_scheme", "EM/{priority}/{semantic}")
    push_levels = set(rules.get("discord_push_levels", ["URGENT", "ACTION"]))

    try:
        pw = resolve_app_pw(resolve_cred, acct.get("cred_path", ""))
    except Exception as e:
        log("ACCOUNT %s: cred resolve FAILED: %s" % (slug, e))
        return {"account": slug, "error": "cred"}
    if not pw:
        log("ACCOUNT %s: no app pw resolved (skipped)" % slug)
        return {"account": slug, "error": "no-pw"}

    os.environ["GMAIL_APP_PW"] = pw
    try:
        state = em_watch.load_state(state_path)
        key = "%s::INBOX" % user
        cursor = state["cursors"].get(key, {"uidvalidity": None, "last_uid": 0})
        seen = set(state.get("seen_gm_msgids", []))
        records, new_cursor = em_watch.run_once(user, "INBOX", cursor, acct.get("max_batch", 400))
    finally:
        os.environ.pop("GMAIL_APP_PW", None)

    n_new = n_alert = n_archive = 0
    for r in records:
        gid = r.get("gm_msgid")
        if gid and gid in seen:
            continue
        if gid:
            seen.add(gid)
        n_new += 1
        msg = {"from": r["from"], "subject": r["subject"], "account": slug,
               "list_unsubscribe": r.get("list_unsubscribe", False)}
        cls = em_classify.classify(msg, rules)
        pr, label = cls["priority"], cls["label"]
        full_label = label_scheme.replace("{priority}", pr).replace("{semantic}", label)

        if pr in push_levels and not dry:
            try:
                em_alert.send(em_alert.build_title(pr, slug, r["subject"]))
                n_alert += 1
            except Exception as e:
                log("ACCOUNT %s: alert failed: %s" % (slug, e))

        # pool upsert for actionable; FYI logged but still tracked; NOISE archived silently
        if pr in ("URGENT", "ACTION", "FYI"):
            try:
                title = derive_title(pr, label, r["subject"])
                em_pool.upsert(reminder, db, r["message_id"], r["thread_key"], title,
                               kind="task" if pr in ("URGENT", "ACTION") else "event",
                               priority=2 if pr == "URGENT" else (4 if pr == "ACTION" else 7),
                               tags=["acct:%s" % slug, label],
                               ext_extra={"account": slug, "uid": r["uid"],
                                          "subject_raw": r["subject"], "from": r["from"],
                                          "label": full_label, "priority_tier": cls["tier"]})
            except Exception as e:
                log("ACCOUNT %s: pool upsert failed: %s" % (slug, e))
        if pr == "NOISE":
            archive(user, gid, full_label, dry)
            n_archive += 1

    state["cursors"][key] = new_cursor
    state["seen_gm_msgids"] = sorted(seen)[-50000:]
    em_watch.save_state(state_path, state)
    log("ACCOUNT %s: new=%d alert=%d archived=%d cursor_uid=%d"
        % (slug, n_new, n_alert, n_archive, new_cursor["last_uid"]))
    return {"account": slug, "new": n_new, "alert": n_alert, "archived": n_archive}


def derive_title(priority, label, subject):
    """ASCII imperative one-liner (no PII-heavy raw subject)."""
    base = em_alert.redact_subject(subject, max_words=8)
    verb = "Reply to" if priority in ("URGENT", "ACTION") else "Review"
    title = "%s mail re %s" % (verb, base or "item")
    return "".join(ch for ch in title if ord(ch) < 128)[:120]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="path to registry.json (companion config)")
    ap.add_argument("--rules", help="merged rules JSON (global + personal). default: alongside config")
    ap.add_argument("--db", default=None)
    ap.add_argument("--reminder", default=em_pool.default_reminder_path())
    ap.add_argument("--resolve-cred", help="path to resolve-cred.ps1 (DPAPI). omit in tests")
    ap.add_argument("--state-dir", default=os.path.expanduser(
        os.path.join("~", ".claude", "email-monitor", "state")))
    ap.add_argument("--summary", default=os.path.join(HERE, "em_summary.py"))
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    with open(a.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    rules = {}
    rules_path = a.rules or os.path.join(os.path.dirname(a.config), "rules", "merged.json")
    if os.path.isfile(rules_path):
        with open(rules_path, "r", encoding="utf-8") as f:
            rules = json.load(f)

    missing = preflight(a.reminder, a.resolve_cred)
    if missing:
        msg = "[email-monitor] preflight FAILED, missing: %s" % ", ".join(missing)
        log(msg)
        try:
            em_alert.send(msg)
        except Exception:
            pass
        return 2

    os.makedirs(a.state_dir, exist_ok=True)
    results = []
    for acct in cfg.get("accounts", []):
        try:
            results.append(process_account(acct, rules, a.reminder, a.db,
                                           a.resolve_cred, a.state_dir, a.dry))
        except Exception as e:
            log("ACCOUNT %s: UNCAUGHT %s" % (acct.get("slug", "?"), e))
            results.append({"account": acct.get("slug", "?"), "error": str(e)})

    # daily-summary due check (read-only); worker produces content, tick does not
    try:
        d = em_pool.due(a.reminder, a.db)
        for it in d.get("items", []):
            ext = it.get("ext") or {}
            if ext.get("x_email_monitor_kind") == "daily-summary":
                log("daily-summary due -> running worker")
                if not a.dry:
                    subprocess.run([sys.executable, a.summary, "--config", a.config,
                                    "--reminder", a.reminder] + (["--db", a.db] if a.db else []),
                                   capture_output=True, text=True, encoding="utf-8")
    except Exception as e:
        log("daily-summary check failed: %s" % e)

    print(json.dumps({"results": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
