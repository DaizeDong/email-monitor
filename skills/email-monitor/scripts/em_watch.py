#!/usr/bin/env python3
"""email-monitor incremental IMAP watcher (stdlib imaplib only).

The monitoring spine = heartbeat + reconciliation, NOT a bare IDLE daemon (avoids the silent-stall
missed-mail failure, ARCHITECTURE §2.1 / anti-pattern #7). One tick: connect read-only, compute the
new-UID range from a persisted watermark, PEEK headers (never set \\Seen), emit one JSON record per
new mail, advance the watermark.

Correctness invariants (every one cross-checked >=2 sources):
  - anchor = (UIDVALIDITY, last_uid) per account+folder; NEVER sequence numbers, NEVER SEARCH SINCE.
  - read-only EXAMINE/SELECT(readonly=True) + BODY.PEEK[HEADER.FIELDS ...]  (no \\Seen side effect).
  - new range = UID (last_uid+1):*  ; cap batch size.
  - UIDVALIDITY changed -> re-baseline last_uid = UIDNEXT-1 (do not backfill the whole mailbox).
  - dedupe anchor = X-GM-MSGID (64-bit, stable across folders/labels).
  - anchor INBOX (small), not All Mail (anti-pattern #9).
  - credential from env GMAIL_APP_PW only; never argv / log / echo.

The pure functions (compute_fetch_range, parse_header_fetch, advance_cursor) are unit-tested with a
fake IMAP so watermark logic is provable without a live server.

Usage:
  GMAIL_APP_PW=... python em_watch.py --user you@example.com --state state.json [--max-batch 400] [--json]
Stdlib only.
"""
import argparse
import email
import imaplib
import json
import os
import re
import sys
from email.header import decode_header, make_header

imaplib._MAXLINE = 1000000
HEADER_FIELDS = "(FROM SUBJECT DATE MESSAGE-ID REFERENCES IN-REPLY-TO LIST-UNSUBSCRIBE)"
BODY_MAX = 50000  # cap extracted body text (chars); guards a runaway newsletter from blowing tokens


def dec(s):
    try:
        return str(make_header(decode_header(s)))
    except Exception:
        return s or ""


def _strip_html(html):
    """Very small HTML->text: drop script/style, tags, collapse whitespace. No deps."""
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = re.sub(r"&nbsp;", " ", html)
    html = re.sub(r"&amp;", "&", html)
    html = re.sub(r"&lt;", "<", html).replace("&gt;", ">").replace("&#39;", "'").replace("&quot;", '"')
    return re.sub(r"[ \t]*\n[ \t]*", "\n", re.sub(r"[ \t]+", " ", html)).strip()


def extract_body(msg):
    """Best-effort plain-text body from a parsed email.message. Prefer text/plain; fall back to
    stripped text/html. Skip attachments and non-text parts. Returns "" on failure. Capped."""
    def payload_text(part):
        try:
            raw = part.get_payload(decode=True)
            if raw is None:
                return ""
            charset = part.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace")
        except Exception:
            return ""
    try:
        plains, htmls = [], []
        if msg.is_multipart():
            for part in msg.walk():
                if part.is_multipart():
                    continue
                disp = (part.get("Content-Disposition") or "").lower()
                if "attachment" in disp:
                    continue
                ctype = (part.get_content_type() or "").lower()
                if ctype == "text/plain":
                    plains.append(payload_text(part))
                elif ctype == "text/html":
                    htmls.append(payload_text(part))
        else:
            ctype = (msg.get_content_type() or "").lower()
            if ctype == "text/html":
                htmls.append(payload_text(msg))
            else:
                plains.append(payload_text(msg))
        text = "\n".join(t for t in plains if t).strip()
        if not text:
            text = "\n".join(_strip_html(h) for h in htmls if h).strip()
        return text[:BODY_MAX]
    except Exception:
        return ""


# ---------- pure, unit-testable core ----------

def compute_fetch_range(cursor, uidvalidity, uidnext, max_batch=400):
    """Decide which UID range to fetch this tick.

    Returns (lo, hi, rebaselined): inclusive UID range to FETCH, or (None, None, ...) if nothing new.
    cursor = {"uidvalidity": int|None, "last_uid": int} (last_uid may be 0 for fresh).
    """
    rebaselined = False
    prev_validity = cursor.get("uidvalidity")
    last_uid = int(cursor.get("last_uid", 0) or 0)
    if prev_validity is not None and int(prev_validity) != int(uidvalidity):
        # UIDVALIDITY rotated: old UIDs meaningless -> re-baseline to current tip, no backfill.
        return None, None, True
    if last_uid == 0 and prev_validity is None:
        # first ever run: baseline to tip so we only act on mail arriving AFTER setup.
        return None, None, True
    lo = last_uid + 1
    hi = max(uidnext - 1, last_uid)
    if hi < lo:
        return None, None, rebaselined
    if hi - lo + 1 > max_batch:
        hi = lo + max_batch - 1
    return lo, hi, rebaselined


def advance_cursor(uidvalidity, uidnext, fetched_uids, prev_last_uid, rebaselined):
    """Compute the next watermark. On rebaseline, jump to UIDNEXT-1. Else max fetched."""
    if rebaselined:
        return {"uidvalidity": int(uidvalidity), "last_uid": max(int(uidnext) - 1, 0)}
    nlast = prev_last_uid
    if fetched_uids:
        nlast = max(prev_last_uid, max(fetched_uids))
    return {"uidvalidity": int(uidvalidity), "last_uid": int(nlast)}


def parse_header_fetch(raw_headers, uid, gm_msgid=None, gm_thrid=None):
    """Parse a fetched RFC822 blob into the emitted record. If the blob carries a body (full-message
    fetch), `body` holds best-effort plain text; header-only fetches yield body=""."""
    msg = email.message_from_bytes(raw_headers) if isinstance(raw_headers, bytes) \
        else email.message_from_string(raw_headers)
    frm = dec(msg.get("From", ""))
    subj = dec(msg.get("Subject", ""))
    date = msg.get("Date", "")
    mid = (msg.get("Message-ID") or msg.get("Message-Id") or "").strip()
    refs = (msg.get("References") or "").strip()
    irt = (msg.get("In-Reply-To") or "").strip()
    lu = bool(msg.get("List-Unsubscribe"))
    thread_key = compute_thread_key(mid, refs, irt, gm_thrid)
    return {
        "uid": int(uid),
        "message_id": mid,
        "gm_msgid": gm_msgid,
        "thread_key": thread_key,
        "from": frm,
        "subject": subj,
        "date": date,
        "list_unsubscribe": lu,
        "body": extract_body(msg),
    }


def compute_thread_key(message_id, references, in_reply_to, gm_thrid=None):
    """Stable thread root. Prefer Gmail thrid; else References root; else In-Reply-To; else self."""
    if gm_thrid:
        return "thrid:%s" % gm_thrid
    refs = re.findall(r"<[^>]+>", references or "")
    if refs:
        return "ref:%s" % refs[0]
    irt = re.findall(r"<[^>]+>", in_reply_to or "")
    if irt:
        return "ref:%s" % irt[0]
    return "ref:%s" % (message_id or "unknown")


# ---------- seen-set bounding (pure, unit-testable) ----------

def bound_seen(seen, cap=50000):
    """Keep the NEWEST `cap` X-GM-MSGIDs, dropping the oldest.

    X-GM-MSGID is a monotonically-increasing 64-bit integer per account, so the
    numerically-largest ids are the newest. The previous `sorted(seen)[-cap:]` sorted
    LEXICOGRAPHICALLY, which for unequal-length integers drops the wrong elements
    (e.g. "1000" < "2" as strings) and could discard a recent id -> re-report a mail
    already processed. Sort by integer value; non-numeric ids sort oldest (least).
    """
    def keyf(m):
        s = str(m)
        return (1, int(s)) if s.isdigit() else (0, 0)
    return sorted(seen, key=keyf)[-cap:] if cap and cap > 0 else sorted(seen, key=keyf)


# ---------- state persistence ----------

def load_state(path):
    if path and os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"cursors": {}, "seen_gm_msgids": []}


def save_state(path, state):
    if not path:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------- live IMAP (thin; logic above is what tests cover) ----------

def _x_attr(item_bytes, name):
    m = re.search(name.encode() + rb"\s+(\d+)", item_bytes)
    return m.group(1).decode() if m else None


def run_once(user, folder, cursor, max_batch=400):
    """Connect read-only, fetch new headers, return (records, new_cursor). Live side effects only."""
    pw = os.environ.get("GMAIL_APP_PW")
    if not pw:
        raise RuntimeError("GMAIL_APP_PW not set (DPAPI-resolved at runtime; never on argv)")
    M = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        M.login(user, pw)
        typ, data = M.select(folder, readonly=True)  # readonly -> no \\Seen
        if typ != "OK":
            raise RuntimeError("select %s failed: %r" % (folder, data))
        status, sd = M.status(folder, "(UIDVALIDITY UIDNEXT)")
        sd0 = sd[0].decode() if sd and sd[0] else ""
        uidvalidity = int(re.search(r"UIDVALIDITY (\d+)", sd0).group(1))
        uidnext = int(re.search(r"UIDNEXT (\d+)", sd0).group(1))
        lo, hi, rebaselined = compute_fetch_range(cursor, uidvalidity, uidnext, max_batch)
        records, fetched = [], []
        if lo is not None:
            # Full message (PEEK -> no \\Seen) so the classifier agent gets the real body.
            typ, fd = M.uid("FETCH", "%d:%d" % (lo, hi),
                            "(X-GM-MSGID X-GM-THRID BODY.PEEK[])")
            if typ == "OK":
                for item in fd:
                    if not isinstance(item, tuple):
                        continue
                    meta, raw = item[0], item[1]
                    uid_m = re.search(rb"UID (\d+)", meta)
                    uid = int(uid_m.group(1)) if uid_m else None
                    gm_msgid = _x_attr(meta, "X-GM-MSGID")
                    gm_thrid = _x_attr(meta, "X-GM-THRID")
                    if uid is None:
                        continue
                    rec = parse_header_fetch(raw, uid, gm_msgid, gm_thrid)
                    records.append(rec)
                    fetched.append(uid)
        new_cursor = advance_cursor(uidvalidity, uidnext, fetched,
                                    int(cursor.get("last_uid", 0) or 0), rebaselined)
        return records, new_cursor
    finally:
        try:
            M.logout()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True)
    ap.add_argument("--folder", default="INBOX")
    ap.add_argument("--state", required=True, help="per-account state JSON path (gitignored)")
    ap.add_argument("--max-batch", type=int, default=400)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    state = load_state(a.state)
    key = "%s::%s" % (a.user, a.folder)
    cursor = state["cursors"].get(key, {"uidvalidity": None, "last_uid": 0})
    seen = set(state.get("seen_gm_msgids", []))

    records, new_cursor = run_once(a.user, a.folder, cursor, a.max_batch)
    # X-GM-MSGID dedupe (gate: cross-folder / relabel never re-analyzed)
    fresh = []
    for r in records:
        gid = r.get("gm_msgid")
        if gid and gid in seen:
            continue
        if gid:
            seen.add(gid)
        fresh.append(r)

    state["cursors"][key] = new_cursor
    state["seen_gm_msgids"] = bound_seen(seen, 50000)  # keep newest by msgid value
    save_state(a.state, state)

    for r in fresh:
        # redaction note: from/subject are local-only audit fields; never forwarded to web/Discord.
        print(json.dumps(r, ensure_ascii=False))
    if a.json:
        print(json.dumps({"new_count": len(fresh), "cursor": new_cursor}, ensure_ascii=False),
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
