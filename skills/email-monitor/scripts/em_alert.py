#!/usr/bin/env python3
"""Build a Chinese alert title and send it through a configured notifier.

The title contains the configured account label and a bounded classifier gist,
or coarse subject words when no gist exists. Gist redaction removes recognized
addresses, URLs and token patterns; ordinary names, dates and amounts can remain.
It is not a guarantee that all personal information or credentials are removed.
Title construction does not include the email body. The account label is retained.

``send`` and CLI ``--message`` pass their message through unchanged: their callers
must prepare the alert text. Recurring reminders use the base tick separately.
The notifier subprocess owns relay credentials.
"""
import argparse
import json
import os
import re
import subprocess
import sys

import em_actions

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
# Pure digit and pure letter runs are retained for readability.
CODE_RE = re.compile(r"\b(?=[A-Za-z0-9]*[A-Za-z])(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{6,}\b")
BLOB_RE = re.compile(r"[A-Za-z0-9]{19,}")

PRIORITY_ZH = {"URGENT": "紧急", "ACTION": "待办", "FYI": "知悉", "NOISE": "噪音"}
# NOTE: no account map lives here on purpose. The human-friendly label for a mailbox is PII, so it
# comes from the private companion config (`accounts[].display_zh` in registry.json) and is passed
# in as `account_label`. This repo is public and must never carry a real account name.


def redact_subject(subject, max_words=6):
    """Coarse, best-effort keyword hint from a subject. Strips recognized emails, URLs/domains,
    order/number IDs, and any alphanumeric token containing a digit (secrets/tokens/tracking/
    confirmation codes) or over-long blob. CJK is preserved (see CJK_RE). Residual pure-alpha words
    (including proper nouns) may remain. A short subject may survive unchanged.

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
    """Remove recognized address, URL and token patterns from a bounded gist.

    Names, dates, amounts and pure digit runs can remain. Pattern matching does
    not guarantee removal of every credential or personal detail.
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


def send(message, idempotency_key=None, python=None):
    cmd = _egress_cmd()
    if not cmd:
        raise RuntimeError("no relay available (neither schedule-reminder relay.py nor %s)" % RELAY)
    if python:
        cmd[0] = python
    args = cmd + [message]
    if idempotency_key:
        # relay.py send prints its delivery receipt for this key and names the adapter given here.
        # (A bare `--json` here would be read as relay.py's payload option and refused.)
        args += ["--idempotency-key", idempotency_key, "--receipt-adapter", "alert"]
    p = subprocess.run(args,
                       capture_output=True, text=True, encoding="utf-8", **_NOWINDOW)
    receipt = None
    if idempotency_key is not None:
        try:
            receipt = json.loads((p.stdout or "").strip())
        except (ValueError, TypeError):
            pass
        # A helper can fail while proving it did not deliver. Preserve that
        # matching proof so callers can retry; a nonzero delivery claim cannot
        # establish success.
        if em_actions.receipt_status(receipt, idempotency_key, "alert") == "not_applied":
            return receipt
    if p.returncode != 0:
        raise RuntimeError("relay failed: %s" % (p.stderr or p.stdout))
    if idempotency_key is None:
        return True
    # Only the downstream service can supply a verifiable delivery receipt.
    # In particular, rc=0 from a legacy relay does not establish completion.
    return receipt


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
