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
import re
import subprocess
import sys

# On Windows, child console apps (powershell, python) flash a console window even
# when the parent runs under pythonw. CREATE_NO_WINDOW keeps every tick invisible.
_NOWINDOW = {"creationflags": 0x08000000} if sys.platform == "win32" else {}

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import em_classify        # noqa: E402
import em_agent_classify  # noqa: E402
import em_pool            # noqa: E402
import em_alert           # noqa: E402
import em_watch           # noqa: E402

LOG = os.path.expanduser(os.path.join("~", ".claude", "logs", "email-monitor.log"))
LABEL_TOOL = os.path.expanduser(os.path.join("~", ".claude", "scripts", "gmail-imap-label.py"))

ENV_VAR = "EMAIL_MONITOR_CONFIG"


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
                       capture_output=True, text=True, encoding="utf-8", **_NOWINDOW)
    if p.returncode != 0:
        raise RuntimeError("resolve-cred failed (rc=%d)" % p.returncode)
    return (p.stdout or "").strip()


def archive(user, rfc_msgid, label, dry, app_pw=None):
    """Archive via the existing bulk tool: add label + de-inbox, selected by RFC822 Message-ID.

    `rfc_msgid` MUST be the RFC822 `Message-ID` header (`r["message_id"]`), not Gmail's internal
    X-GM-MSGID (`r["gm_msgid"]`). Gmail's `rfc822msgid:` operator only matches the former; feeding
    it an X-GM-MSGID matches zero messages, and the label tool then prints "nothing to do" and
    exits 0 -- which used to be counted as a successful archive (a phantom).

    The label tool authenticates from GMAIL_APP_PW. That secret is injected into *this child's*
    environment only -- never left in os.environ, because the surrounding tick also spawns the
    classifier CLIs (codex/cc/claude) and the Gmail password must not leak into them.

    Returns True only if the child exited 0 AND actually matched a message. Anything else is a
    failure, so the NOISE counter can never lie. In --dry the planned action is a no-op success.
    """
    if not rfc_msgid:
        return False
    mid = str(rfc_msgid).strip().strip("<>")
    query = "rfc822msgid:%s" % mid  # gmail search by Message-ID; precise single-message select
    args = [sys.executable, LABEL_TOOL, "--user", user, "--query", query,
            "--add", label, "--archive"]
    if dry:
        args.append("--dry")
    env = dict(os.environ)
    if app_pw:
        env["GMAIL_APP_PW"] = app_pw
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", env=env,
                       **_NOWINDOW)
    if p.returncode != 0:
        log("ACCOUNT %s: archive FAILED (rc=%d) msgid=%s err=%s"
            % (user, p.returncode, mid, (p.stderr or p.stdout or "").strip()[:120]))
        return False
    m = re.search(r"matched (\d+) messages", p.stdout or "")
    if m and int(m.group(1)) == 0:
        log("ACCOUNT %s: archive MATCHED 0 msgid=%s (not archived)" % (user, mid))
        return False
    return True


def classify_record(msg, rules, agent_cfg):
    """Primary path: agent judgment via a cost-ordered provider chain (codex -> cc -> claude). Fall
    back to the deterministic heuristic when disabled or every provider fails, so a tick never goes
    silent. Returns a classify-shaped dict."""
    if agent_cfg.get("mode", "agent") == "agent":
        cls = em_agent_classify.classify(
            msg,
            chain=agent_cfg.get("chain"),
            providers=agent_cfg.get("providers"),
            timeout=int(agent_cfg.get("timeout_sec", 180)),
            owner=agent_cfg.get("owner", ""),
            log=log)
        if cls is not None:
            return cls
        log("ACCOUNT %s: all agent providers failed -> heuristic fallback"
            % msg.get("account", "?"))
    return em_classify.classify(msg, rules)


def process_account(acct, rules, reminder, db, resolve_cred, state_dir, dry, agent_cfg=None,
                    archive_enabled=True):
    agent_cfg = agent_cfg or {}
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

    n_new = n_alert = n_archive = n_kept = 0
    for r in records:
        gid = r.get("gm_msgid")
        if gid and gid in seen:
            continue
        if gid:
            seen.add(gid)
        n_new += 1
        msg = {"from": r["from"], "subject": r["subject"], "account": slug,
               "list_unsubscribe": r.get("list_unsubscribe", False),
               "body": r.get("body", "")}
        cls = classify_record(msg, rules, agent_cfg)
        pr, label = cls["priority"], cls["label"]
        full_label = label_scheme.replace("{priority}", pr).replace("{semantic}", label)

        if pr in push_levels and not dry:
            try:
                em_alert.send(em_alert.build_title(
                    pr, slug, r["subject"],
                    summary=cls.get("summary_zh", ""),
                    account_label=acct.get("display_zh")))
                n_alert += 1
            except Exception as e:
                log("ACCOUNT %s: alert failed: %s" % (slug, e))

        # pool upsert for actionable; FYI logged but still tracked; NOISE archived silently
        if pr in ("URGENT", "ACTION", "FYI"):
            try:
                title = derive_title(pr, label, r["subject"], cls.get("summary_zh", ""))
                em_pool.upsert(reminder, db, r["message_id"], r["thread_key"], title,
                               kind="task" if pr in ("URGENT", "ACTION") else "event",
                               priority=2 if pr == "URGENT" else (4 if pr == "ACTION" else 7),
                               tags=["acct:%s" % slug, label],
                               ext_extra={"account": slug, "uid": r["uid"],
                                          "subject_raw": r["subject"], "from": r["from"],
                                          "label": full_label, "priority_tier": cls["tier"]})
            except Exception as e:
                log("ACCOUNT %s: pool upsert failed: %s" % (slug, e))
        if pr == "NOISE" and archive_enabled:
            # rfc822msgid: matches the RFC822 Message-ID header, NOT gm_msgid (X-GM-MSGID)
            if archive(user, r.get("message_id"), full_label, dry, app_pw=pw):
                n_archive += 1
        elif pr == "NOISE":
            n_kept += 1  # archiving off: NOISE stays in the INBOX for the owner to see

    state["cursors"][key] = new_cursor
    state["seen_gm_msgids"] = em_watch.bound_seen(seen, 50000)  # newest by msgid value
    em_watch.save_state(state_path, state)
    log("ACCOUNT %s: new=%d alert=%d archived=%d kept_in_inbox=%d cursor_uid=%d"
        % (slug, n_new, n_alert, n_archive, n_kept, new_cursor["last_uid"]))
    return {"account": slug, "new": n_new, "alert": n_alert, "archived": n_archive,
            "kept": n_kept}


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
    ap.add_argument("--config", default=None, help="path to registry.json (companion config). "
                    "If omitted, resolved via $EMAIL_MONITOR_CONFIG -> _DIR -> "
                    "~/.email-monitor-config/ -> ~/.config/email-monitor-config/ (config-spec E2).")
    ap.add_argument("--rules", help="merged rules JSON (global + personal). default: alongside config")
    ap.add_argument("--db", default=None)
    ap.add_argument("--reminder", default=em_pool.default_reminder_path())
    ap.add_argument("--resolve-cred", help="path to resolve-cred.ps1 (DPAPI). omit in tests")
    ap.add_argument("--state-dir", default=os.path.expanduser(
        os.path.join("~", ".claude", "email-monitor", "state")))
    ap.add_argument("--summary", default=os.path.join(HERE, "em_summary.py"))
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    a.config = resolve_config(a.config)
    if not a.config or not os.path.isfile(a.config):
        msg = ("[email-monitor] no config found. Set %s=<dir> (with registry.json), pass "
               "--config <registry.json>, or run scripts/init_config.py." % ENV_VAR)
        log(msg)
        print(msg)
        return 2

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

    agent_cfg = cfg.get("classifier", {}) or {}
    log("classifier mode=%s chain=%s" % (agent_cfg.get("mode", "agent"),
                                         ",".join(agent_cfg.get("chain") or em_agent_classify.DEFAULT_CHAIN)))

    # Archiving is opt-out: when disabled, NOISE is still classified/labelled in the pool but the
    # message is never moved out of the INBOX -- the owner reviews every mail themselves. Logged
    # every tick so "nothing is being archived" is never a silent surprise.
    archive_enabled = bool((cfg.get("archive", {}) or {}).get("enabled", True))
    log("archive=%s" % ("enabled" if archive_enabled
                        else "DISABLED (all mail stays in INBOX)"))

    os.makedirs(a.state_dir, exist_ok=True)
    results = []
    for acct in cfg.get("accounts", []):
        try:
            results.append(process_account(acct, rules, a.reminder, a.db,
                                           a.resolve_cred, a.state_dir, a.dry, agent_cfg,
                                           archive_enabled=archive_enabled))
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
                                   capture_output=True, text=True, encoding="utf-8", **_NOWINDOW)
    except Exception as e:
        log("daily-summary check failed: %s" % e)

    print(json.dumps({"results": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
