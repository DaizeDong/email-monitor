#!/usr/bin/env python3
"""email-monitor Discord alert (redacted) -> wraps a local notifier script (the Discord relay).

Privacy red line: only a REDACTED title is pushed. NEVER the body, the raw subject, or any PII
(ARCHITECTURE §2.3, anti-pattern #17). Immediate "new important mail" pings go here; recurring
due/overdue reminders go through the base `tick` instead (no double-notify).

The redacted title is an ASCII imperative line: "[URGENT] <account> <short-subject-token>".
Subject is reduced to a coarse keyword hint (first <=6 ASCII words) with these stripped:
digits/number runs, order/case/ticket/invoice IDs, **email addresses**, **URLs/domains**, and
**any alphanumeric token containing a digit** (secrets/tokens/tracking/confirmation codes) plus
over-long blobs. This is a best-effort hint — the body and raw subject are NEVER egressed; residual
generic words / proper nouns may remain (they reach only the user's own private Discord).

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

_NOWINDOW = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
RELAY = os.path.expanduser(os.environ.get("EMAIL_MONITOR_NOTIFIER", "~/.local/notifier.py"))
ORDER_RE = re.compile(r"\b(?:order|case|ticket|inv|invoice|#)\s*[:#]?\s*\w*\d\w*", re.I)
NUM_RE = re.compile(r"\b\d[\d,.\-]*\b")
EMAIL_RE = re.compile(r"\S+@\S+")
URL_RE = re.compile(r"(?:https?://|www\.)\S+|\b\S+\.(?:com|net|org|io|ai|co|edu|gov|us|uk|dev|app)\b", re.I)
_MAX_TOKEN = 18  # tokens longer than this are treated as opaque ids/blobs and dropped

# CJK + kana. The old redactor kept only ASCII, which deleted a Chinese subject *entirely*, every
# Chinese mail therefore pushed the literal words "new mail". Chinese must survive redaction.
CJK_RE = re.compile(r"[㐀-䶿一-鿿぀-ヿ]")
PUNCT_RE = re.compile(r"[^A-Za-z0-9 㐀-䶿一-鿿぀-ヿ]+")
# a run of >=6 alphanumerics containing BOTH a letter and a digit = code / token / tracking number.
# Pure digits (2026, 400) and pure letters (COBRA) are kept, they carry the meaning.
CODE_RE = re.compile(r"\b(?=[A-Za-z0-9]*[A-Za-z])(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{6,}\b")
BLOB_RE = re.compile(r"[A-Za-z0-9]{19,}")

PRIORITY_ZH = {"URGENT": "紧急", "ACTION": "待办", "FYI": "知悉", "NOISE": "噪音"}
# NOTE: no account map lives here on purpose. The human-friendly label for a mailbox is PII, so it
# comes from the private companion config (`accounts[].display_zh` in registry.json) and is passed
# in as `account_label`. This repo is public and must never carry a real account name.


def redact_subject(subject, max_words=6):
    """Coarse, best-effort keyword hint — never the body/raw subject. Strips emails, URLs/domains,
    order/number IDs, and any alphanumeric token containing a digit (secrets/tokens/tracking/
    confirmation codes) or over-long blob. CJK is preserved (see CJK_RE). Residual pure-alpha words
    (incl. proper nouns) may remain; they reach only the user's own private Discord.

    This is now the FALLBACK path: when the classifier returns a `summary_zh`, `build_title` pushes
    that instead (far more useful). This still runs whenever the agent produced no summary.
    """
    s = subject or ""
    s = EMAIL_RE.sub(" ", s)                             # email addresses (before punct strip)
    s = URL_RE.sub(" ", s)                               # urls / bare domains
    s = ORDER_RE.sub(" ", s)                             # order/case/ticket/inv ids
    s = NUM_RE.sub(" ", s)                               # digit-leading number runs
    s = "".join(ch for ch in s if ord(ch) < 128 or CJK_RE.match(ch))
    s = PUNCT_RE.sub(" ", s)                             # drop punctuation, keep CJK + alnum
    words = []
    for w in s.split():
        if not w:
            continue
        if any(c.isdigit() for c in w):                 # any token with a digit = secret/id/code
            continue
        if CJK_RE.search(w):                             # a CJK run has no spaces: keep it, bounded
            words.append(w[:24])
            continue
        if len(w) > _MAX_TOKEN:                          # opaque blob / base64
            continue
        words.append(w)
    return " ".join(words[:max_words]) if words else "新邮件"


def redact_push(text, limit=60):
    """Strip secrets from the classifier's Chinese gist before it leaves the machine.

    The owner explicitly opted in (2026-07-13) to having the *gist* pushed, because a redacted
    keyword fragment was unreadable and real tasks were being missed. So names, dates and amounts
    deliberately survive — they are the point. What must never ride along is a credential: an email
    address, a URL, or a code/token/tracking number (a >=6 char run mixing letters and digits).
    """
    s = text or ""
    s = EMAIL_RE.sub(" ", s)
    s = URL_RE.sub(" ", s)
    s = BLOB_RE.sub("(见邮箱)", s)      # long opaque blob / base64
    s = CODE_RE.sub("(见邮箱)", s)      # verification code / token / tracking number
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s[:limit]


def build_title(priority, account, subject, summary="", account_label=None):
    """The one line the owner reads on their phone. Chinese frame + the agent's Chinese gist.

    `account_label` is the owner's own name for the mailbox (registry.json `display_zh`); without
    it we fall back to the raw slug, never to a hardcoded map (see the note above).
    """
    pr = priority if priority in ("URGENT", "ACTION", "FYI", "NOISE") else "ACTION"
    acct = (account_label or "".join(ch for ch in (account or "") if ord(ch) < 128) or "邮件")
    gist = redact_push(summary) if (summary or "").strip() else redact_subject(subject)
    return "【%s】%s:%s" % (PRIORITY_ZH[pr], acct, gist)


def _egress_cmd():
    """Pluggable notifier egress: prefer the base reminder tool's unified relay (#mail stream), set
    via $SCHEDULE_RELAY_PY, when it is installed; fall back to a standalone notifier ($EMAIL_MONITOR_
    NOTIFIER) so this skill works on its own. The message text is appended by the caller as the final
    arg (works for both `relay.py send --stream mail --text <msg>` and `notifier.py <msg>`)."""
    rp = os.path.expanduser(os.environ.get("SCHEDULE_RELAY_PY", "~/.local/relay.py"))
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
                       capture_output=True, text=True, encoding="utf-8", **_NOWINDOW)
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
