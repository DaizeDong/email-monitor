#!/usr/bin/env python3
"""email-monitor Discord alert (redacted) -> wraps the notifier.

Privacy red line: only a REDACTED title is pushed. NEVER the body, the raw subject, or any PII
(ARCHITECTURE §2.3, anti-pattern #17). Immediate "new important mail" pings go here; recurring
due/overdue reminders go through the base `tick` instead (no double-notify).

The redacted title is an ASCII imperative line: "[URGENT] <account> <short-subject-token>".
Subject is reduced to a coarse token (first <=6 ASCII words, stripped of digits/order-ids).

Usage:
  python em_alert.py --priority URGENT --account user1 --subject "Payment failed on order 12345"
  python em_alert.py --message "@file.txt"   # pass-through to relay (already-redacted summary)
Stdlib only (calls relay via subprocess so the bot token never enters this process).
"""
import argparse
import os
import re
import subprocess
import sys

RELAY = os.path.expanduser(os.path.join("~", ".claude", "discord_relay", "send.py"))
ORDER_RE = re.compile(r"\b(?:order|case|ticket|inv|invoice|#)\s*[:#]?\s*\w*\d\w*", re.I)
NUM_RE = re.compile(r"\b\d[\d,.\-]*\b")


def redact_subject(subject, max_words=6):
    s = subject or ""
    s = ORDER_RE.sub("", s)
    s = NUM_RE.sub("", s)
    s = "".join(ch for ch in s if ord(ch) < 128)        # ASCII only
    s = re.sub(r"[^A-Za-z0-9 ]+", " ", s)               # drop punctuation
    words = [w for w in s.split() if w]
    return " ".join(words[:max_words]) if words else "new mail"


def build_title(priority, account, subject):
    pr = priority if priority in ("URGENT", "ACTION", "FYI", "NOISE") else "ACTION"
    acct = "".join(ch for ch in (account or "") if ord(ch) < 128)
    return "[%s] %s: %s" % (pr, acct or "mail", redact_subject(subject))


def _egress_cmd():
    """Pluggable Agent Center egress: prefer schedule-reminder's unified relay (#mail stream) when
    the base is installed; fall back to the Big Brother relay (send.py) so this skill works
    standalone. The message text is appended by the caller as the final arg (works for both
    `relay.py send --stream mail --text <msg>` and `send.py <msg>`)."""
    rp = os.environ.get("SCHEDULE_RELAY_PY") or os.path.expanduser(
        "the base reminder relay")
    if os.path.isfile(rp):
        return [sys.executable, rp, "send", "--stream", "mail", "--text"]
    if os.path.isfile(RELAY):
        return [sys.executable, RELAY]
    return None


def send(message):
    cmd = _egress_cmd()
    if not cmd:
        raise RuntimeError("no relay available (neither schedule-reminder relay.py nor %s)" % RELAY)
    p = subprocess.run(cmd + [message],
                       capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        raise RuntimeError("relay failed: %s" % (p.stderr or p.stdout))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--priority", default="ACTION")
    ap.add_argument("--account", default="")
    ap.add_argument("--subject", default="")
    ap.add_argument("--message", default="", help="explicit already-redacted message (bypass build)")
    ap.add_argument("--dry", action="store_true", help="print title, do not send")
    a = ap.parse_args()
    title = a.message if a.message else build_title(a.priority, a.account, a.subject)
    if a.dry:
        print(title)
        return 0
    send(title)
    print("sent: " + title)
    return 0


if __name__ == "__main__":
    sys.exit(main())
