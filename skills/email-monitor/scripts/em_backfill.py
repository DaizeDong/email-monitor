#!/usr/bin/env python3
"""Apply the deterministic sender map to mail that arrived before the kernel did.

The gap this closes. `em_tick` labels forward only: it walks INBOX from a stored
UID watermark, so every message a mapped sender delivered before topic labelling
went live is unlabelled and always will be. The map knows what those messages
are; nothing has ever asked it.

Scope, and why it stops where it does. This backfills the PRE-GATE only, never
the model. Three reasons, in order of weight:

  1. The pre-gate is deterministic and free. `em_topic.pregate` looks the sender
     up and returns one label, with the address itself as the evidence span. So
     `from:<addr>` selects exactly the messages a live tick would have decided,
     and the answer does not depend on when it is asked. A model pass over years
     of archive is neither cheap nor reproducible.
  2. The map contributes SOURCE labels only. taxonomy.md is explicit that a TYPE
     label is a property of the individual message -- a shop sends order
     confirmations and marketing from one address -- so a sender-keyed rule can
     never settle one. This tool refuses to write any label in `_type_labels`,
     and asserts the map contains none, so a future edit that adds one fails here
     instead of silently filing marketing as a receipt.
  3. Historic mail is where a wrong label is most expensive, because nobody is
     watching it arrive. Confidence has to come from the rule, not from a
     judgement call made about mail from two years ago.

Additive, like the kernel. Only `--add`; never `--remove`, never `--archive`.
The query excludes messages that already carry the label, so a second run is a
no-op and the counts it prints are messages actually changed rather than messages
matched. Nothing here can move a message out of the inbox.

Dry by default. `--commit` is required to write, because the failure mode worth
protecting against is not a crash, it is a confident bulk mislabel that looks
like it worked.

Reads the sender map and the per-account allowed set from the private config; a
label outside that account's allowed set is skipped, exactly as `em_topic.judge`
would drop it. Uses the existing `gmail-imap-label.py`, which already does the
IMAP work (X-GM-RAW search, chunked X-GM-LABELS STORE) and reports how many
messages it matched.

  python em_backfill.py --account acct1 --user user1@example.com           # dry
  python em_backfill.py --account acct1 --user user1@example.com --commit
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import em_topic  # noqa: E402

LABEL_TOOL = os.path.expanduser(os.environ.get(
    "EMAIL_MONITOR_LABEL_TOOL", "~/.local/bin/gmail-imap-label.py"))

# Where the DPAPI-encrypted app password and its resolver live. Both are machine
# layout, not this tool's business, so they are env-overridable and the defaults
# are only a convention -- em_tick reads the same two locations.
CRED_TEMPLATE = os.environ.get("EMAIL_MONITOR_CRED_TEMPLATE",
                               os.path.join("~", ".secrets", "gmail-%s.cred"))
RESOLVE_CRED = os.environ.get(
    "EMAIL_MONITOR_RESOLVE_CRED",
    os.path.join("~", ".email-monitor-config", "scripts", "resolve-cred.ps1"))

_NOWINDOW = {"creationflags": 0x08000000} if sys.platform == "win32" else {}


class UnsafeBackfill(Exception):
    """Raised when the map asks for something a sender-keyed rule may not decide.

    Loud rather than skipped: a type label appearing in the map means the map's
    own invariant has broken, and quietly ignoring it would hide that.
    """


def plan(sender_map, allowed_labels, type_labels):
    """Group mapped senders by label. Returns (plan, skipped).

    `plan` is {label: [addresses]} for labels this account may write.
    `skipped` is [(address, label, reason)] so the caller can print WHY a sender
    was left out. A backfill that silently covers less than the map describes is
    indistinguishable from one that had nothing to do.
    """
    by_label, skipped = {}, []
    allowed = set(allowed_labels or [])
    types = set(type_labels or [])

    for addr, label in sorted((sender_map.get("by_address") or {}).items()):
        if label in types:
            raise UnsafeBackfill(
                "sender %r maps to type label %r; a type label is a property of the "
                "individual message and cannot be settled by sender" % (addr, label))
        if label not in allowed:
            skipped.append((addr, label, "not in this account's allowed set"))
            continue
        by_label.setdefault(label, []).append(addr)
    return by_label, skipped


def build_query(addresses, label):
    """Gmail query selecting mail from these senders that does NOT already carry
    the label. The exclusion is what makes a re-run a no-op and makes the matched
    count mean "messages this run would change"."""
    froms = " OR ".join("from:%s" % a for a in addresses)
    return '{%s} -label:"%s"' % (froms, label)


def run_tool(user, query, label, commit, app_pw=None, runner=None):
    """Invoke the label tool. Returns (matched, ok). Never raises on a tool failure;
    the caller reports it and moves to the next label, because one bad label must
    not abort the rest of the backfill."""
    args = [sys.executable, LABEL_TOOL, "--user", user, "--query", query, "--add", label]
    if not commit:
        args.append("--dry")
    env = dict(os.environ)
    if app_pw:
        env["GMAIL_APP_PW"] = app_pw
    # The label tool prints every matched From/Subject. Under a detached child on
    # Windows sys.stdout encodes with the ANSI codepage, so a subject containing a
    # character GBK cannot represent -- a registered-trademark sign was enough --
    # raises UnicodeEncodeError and the tool exits non-zero AFTER it has already
    # printed "matched N messages". The search had succeeded; only the echo failed.
    # Reading that as a failed chunk would silently skip real mail, so pin the
    # child's stdout to UTF-8 and let it print anything.
    env["PYTHONIOENCODING"] = "utf-8"
    # errors="replace" is load-bearing, not defensive padding. The label tool prints
    # matched subjects, and on Windows a detached child's stdout is decoded with the
    # ANSI codepage, so a Chinese subject arrives as bytes that are not valid UTF-8.
    # Strict decoding raises inside subprocess's reader THREAD, where the exception
    # cannot be caught here: the run dies with a traceback that says nothing about
    # mail. Only the "matched N" count is parsed, and that is pure ASCII, so a
    # mangled subject costs nothing while a hard decode costs the whole backfill.
    run = runner or (lambda a, e: subprocess.run(
        a, capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=e, **_NOWINDOW))
    p = run(args, env)
    out = (getattr(p, "stdout", "") or "")
    if getattr(p, "returncode", 1) != 0:
        return 0, False
    m = re.search(r"matched (\d+) messages", out)
    return (int(m.group(1)) if m else 0), True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--account", required=True, help="account slug as used in labels.json")
    ap.add_argument("--user", required=True, help="the mailbox address to authenticate as")
    ap.add_argument("--commit", action="store_true",
                    help="actually write labels; without this nothing is changed")
    ap.add_argument("--only-label", default=None,
                    help="restrict the run to one label, for a cautious first pass")
    ap.add_argument("--cred", default=None,
                    help="DPAPI .cred to decrypt for GMAIL_APP_PW. Defaults to "
                         "EMAIL_MONITOR_CRED_TEMPLATE %% <account>. Ignored when "
                         "GMAIL_APP_PW is already set in the environment.")
    ap.add_argument("--resolve-cred", default=RESOLVE_CRED,
                    help="the resolver em_tick uses; same path, same DPAPI blob")
    a = ap.parse_args(argv)

    # Resolve the app password the same way em_tick does, so a backfill needs no
    # more privilege than a tick and the secret never has to be typed or pasted.
    # It is passed to the child in its env only, never logged and never printed.
    app_pw = os.environ.get("GMAIL_APP_PW")
    if not app_pw:
        cred = os.path.expanduser(a.cred or (CRED_TEMPLATE % a.account))
        resolver = os.path.expanduser(a.resolve_cred)
        if os.path.isfile(cred) and os.path.isfile(resolver):
            p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                                "-File", resolver, "-CredPath", cred],
                               capture_output=True, text=True, encoding="utf-8", **_NOWINDOW)
            if p.returncode == 0:
                app_pw = (p.stdout or "").strip()
        if not app_pw:
            # Say which half is missing. "no password" and "no resolver" need
            # different fixes, and a single message for both wastes a debugging pass.
            print("cannot resolve GMAIL_APP_PW: cred=%s resolver=%s"
                  % ("present" if os.path.isfile(cred) else "MISSING",
                     "present" if os.path.isfile(resolver) else "MISSING"))
            print("set GMAIL_APP_PW in the environment, or pass --cred/--resolve-cred")
            return 2

    cfg = em_topic.load_config(a.account, log=lambda m: print(m))
    if cfg is None:
        print("no private config for account %r; nothing to do" % a.account)
        return 1

    try:
        by_label, skipped = plan(cfg["sender_map"], cfg["allowed_labels"], cfg["type_labels"])
    except UnsafeBackfill as e:
        print("REFUSING TO RUN: %s" % e)
        return 2

    if a.only_label:
        by_label = {k: v for k, v in by_label.items() if k == a.only_label}

    mode = "COMMIT" if a.commit else "DRY (nothing will be written)"
    print("backfill %s account=%s labels=%d senders=%d skipped=%d"
          % (mode, a.account, len(by_label), sum(len(v) for v in by_label.values()), len(skipped)))

    total, failures = 0, 0
    for label in sorted(by_label):
        addrs = by_label[label]
        # Gmail's query length is bounded, and a 180-sender OR is well past comfortable.
        # Chunk so a big label does not silently truncate to whatever fits.
        for i in range(0, len(addrs), 25):
            chunk = addrs[i:i + 25]
            matched, ok = run_tool(a.user, build_query(chunk, label), label,
                                   a.commit, app_pw)
            if not ok:
                failures += 1
                print("  FAILED  %-26s senders %d-%d" % (label, i + 1, i + len(chunk)))
                continue
            total += matched
            if matched:
                print("  %-7s %-26s %4d message(s) from %d sender(s)"
                      % ("would" if not a.commit else "added", label, matched, len(chunk)))

    for addr, label, why in skipped:
        print("  skipped %s -> %s (%s)" % (addr, label, why))

    print("total %d message(s) %s; %d chunk failure(s)"
          % (total, "would be labelled" if not a.commit else "labelled", failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
