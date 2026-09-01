#!/usr/bin/env python3
"""Adversarial sample review of labels already applied. Reports; never writes.

What this is for, and why it is not the daily watcher. `classification_review.py`
reads the tick's own counters and answers "is the kernel running": how much it
judged, refused, or failed. It cannot answer "is the kernel RIGHT", because a
confidently wrong label and a correct one produce the same counter. Only reading
the mail against the standard answers that, and the only way that has actually
worked here is an independent reader told to REFUTE.

Why refutation rather than review. On 2026-09-01 a reviewer run this way found
47 genuinely wrong labels in a 154-message backfill, and in the same pass
REFUSED to flag conference call-for-papers and marketing newsletters, because
global rule 4 keeps a source label on a genuine sender whatever the content. A
reviewer merely asked to "check the labels" flags those every time. The default
verdict has to be "not a defect" or the report inflates until it is ignored, and
an ignored report is worse than none: it costs the same and buys nothing.

Why monthly and manual-first. Labels drift slowly, sampling costs model calls,
and a finding usually implies editing the sender map or the taxonomy, which is a
judgement call the operator makes. A loop that runs hourly would produce the same
list repeatedly and train the reader to skip it.

Read-only against the mailbox, always. It proposes; it never strips a label.
Today's session settled why: the 47 bad labels came from a WRONG SENDER MAP, and
stripping them without fixing the map would have let the next backfill recreate
every one. Auto-remediation here would loop forever against its own cause.

Gated by `quality_review.enabled` in registry.json, off by default, exactly like
topic_labeling. Off means this exits 0 having done nothing, and says so, because
"disabled" and "found nothing" must never look the same.

  python em_quality_review.py --account <slug> --user <addr>            # report
  python em_quality_review.py --account <slug> --user <addr> --json out.json
  python em_quality_review.py --account <slug> --user <addr> --labels "Work/Scholar,Accounts"
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import em_topic  # noqa: E402

try:
    import llmcall
except Exception:  # pragma: no cover - llmcall is the fleet primitive, absent in bare checkouts
    llmcall = None

LABEL_TOOL = os.path.expanduser(os.environ.get(
    "EMAIL_MONITOR_LABEL_TOOL", "~/.local/bin/gmail-imap-label.py"))
_NOWINDOW = {"creationflags": 0x08000000} if sys.platform == "win32" else {}

DEFAULT_SAMPLE = 40

# The reviewer is handed From and Subject only -- the same two lines the kernel
# judged on. Giving it the body would let it "find" errors the kernel could not
# possibly have avoided, which is not a defect report, it is a complaint about
# the design.
VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer", "description": "the numbered item from the list"},
                    "wrong": {"type": "boolean"},
                    "clause": {"type": "string",
                               "description": "the taxonomy sentence it violates, quoted; empty when wrong is false"},
                    "should_be": {"type": "string"},
                },
                "required": ["n", "wrong", "clause", "should_be"],
            },
        },
    },
    "required": ["findings"],
}

PROMPT = """You are reviewing labels ALREADY APPLIED to email. Your job is to REFUTE each one.

Default to `wrong: false`. Confirm a label is wrong ONLY when you can quote the sentence in
the standard that it violates. A false accusation costs more than a miss here: the operator
acts on this list, and a list that cries wolf gets ignored, which leaves real defects unfound.

THE STANDARD (the only standard; do not invent labels or reinterpret it):
%(taxonomy)s

RULES THAT DECIDE MOST CASES. Read these before judging anything:
- A SOURCE label answers only WHO SENT THIS and WHAT DOMAIN. It does not answer whether the
  content is important. Pure marketing from a genuine sender KEEPS its source label; that is
  the standard's highest-priority rule. "This is just a promotional newsletter" or "this is
  just a call for papers" is NOT grounds to call a label wrong. Only a WRONG DOMAIN is.
- That protection does NOT extend to the `Accounts` parent or to `Life/Receipt`. Those are
  TYPE labels, judged by what the message IS, not by who sent it.
- A service that has a dedicated sublabel must use the sublabel, never the bare `Accounts`.
- Multiple SOURCE labels on one message are forbidden unless it genuinely spans two domains.
- You see only From and Subject, which is all the labeller saw. If those two lines genuinely
  supported the label, it is NOT wrong, even if you suspect the body says otherwise.

MESSAGES, each with the label it currently carries:
%(items)s

Return one entry per numbered item. `clause` must be a verbatim quote from the standard when
`wrong` is true, and empty when it is false.
"""


def load_flag(registry_path):
    """Return (enabled, note). A missing file or key means disabled, never a crash:
    an uninitialised machine should stay inert like the rest of this skill."""
    try:
        with open(os.path.expanduser(registry_path), encoding="utf-8") as fh:
            reg = json.load(fh)
    except Exception:
        return False, "no registry"
    block = reg.get("quality_review") or {}
    return bool(block.get("enabled", False)), block.get("_note", "")


def fetch_labelled(user, label, limit, app_pw, runner=None):
    """List messages carrying `label`, as (from, subject) pairs.

    Uses the existing label tool in --dry mode, which prints matched From/Subject
    and writes nothing. Read-only is not a promise here, it is the only mode used.
    """
    args = [sys.executable, LABEL_TOOL, "--user", user,
            "--query", 'label:"%s"' % label, "--add", label, "--dry"]
    env = dict(os.environ)
    if app_pw:
        env["GMAIL_APP_PW"] = app_pw
    # Same two encoding guards the backfill needed: the tool echoes real subjects,
    # and on Windows a strict decode kills the run inside a reader thread while a
    # GBK-hostile character makes the tool exit non-zero after doing its work.
    env["PYTHONIOENCODING"] = "utf-8"
    run = runner or (lambda a, e: subprocess.run(
        a, capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=e, **_NOWINDOW))
    p = run(args, env)
    if getattr(p, "returncode", 1) != 0:
        return []
    out = []
    for line in (getattr(p, "stdout", "") or "").split("\n"):
        if "|" not in line or line.startswith("matched"):
            continue
        frm, _, subj = line.partition("|")
        frm, subj = frm.strip(), subj.strip()
        if frm and subj:
            out.append((frm, subj))
    return out[:limit] if limit else out


def sample(items, n, stride_seed=0):
    """Spread the sample across the corpus instead of taking the newest N.

    The newest N is the worst possible sample: it is the mail the operator has
    most likely already seen, and it hides exactly the old drift this review is
    for. A fixed stride is used rather than randomness so two runs over an
    unchanged corpus produce the same report and a diff means something.
    """
    if not items or n <= 0:
        return []
    if len(items) <= n:
        return list(items)
    step = len(items) / float(n)
    return [items[int(i * step) + stride_seed % max(1, int(step))] for i in range(n)]


def judge(items, taxonomy, call=None, timeout=300.0):
    """Ask one reviewer to refute the whole batch. Returns [] when the call fails.

    Returning [] on failure rather than raising keeps an outage from being read as
    a clean bill of health -- the caller distinguishes them and says which it was.
    """
    if not items:
        return []
    listing = "\n".join(
        "%d. FROM: %s\n   SUBJECT: %s\n   LABEL: %s" % (i + 1, f, s, l)
        for i, (f, s, l) in enumerate(items))
    prompt = PROMPT % {"taxonomy": taxonomy, "items": listing}
    if call is not None:
        return call(prompt)
    if llmcall is None:
        return None
    r = llmcall.call(prompt, mode="judge", schema=VERDICT_SCHEMA, timeout=timeout)
    if not r:
        return None
    data = getattr(r, "data", None) or {}
    return data.get("findings") or []


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--account", required=True)
    ap.add_argument("--user", required=True)
    ap.add_argument("--labels", default=None,
                    help="comma-separated subset; default is every allowed label")
    ap.add_argument("--sample", type=int, default=DEFAULT_SAMPLE,
                    help="messages sampled per label (default %d)" % DEFAULT_SAMPLE)
    ap.add_argument("--json", default=None, help="also write the findings here")
    ap.add_argument("--registry", default="~/.email-monitor-config/registry.json")
    ap.add_argument("--force", action="store_true",
                    help="run even when quality_review.enabled is false")
    ap.add_argument("--cred", default=None)
    ap.add_argument("--resolve-cred",
                    default=os.path.join("~", ".email-monitor-config", "scripts", "resolve-cred.ps1"))
    a = ap.parse_args(argv)

    enabled, note = load_flag(a.registry)
    if not enabled and not a.force:
        # Saying this out loud matters: a silent exit 0 here is indistinguishable
        # from a run that found nothing wrong.
        print("quality_review is DISABLED in registry.json -- nothing was reviewed.")
        print("Set quality_review.enabled true, or pass --force for a one-off run.")
        return 0

    cfg = em_topic.load_config(a.account, log=lambda m: print(m))
    if cfg is None:
        print("no private config for account %r" % a.account)
        return 1

    labels = [l.strip() for l in a.labels.split(",")] if a.labels else list(cfg["allowed_labels"])

    app_pw = os.environ.get("GMAIL_APP_PW")
    if not app_pw:
        cred = os.path.expanduser(a.cred or os.path.join("~", ".secrets", "gmail-%s.cred" % a.account))
        resolver = os.path.expanduser(a.resolve_cred)
        if os.path.isfile(cred) and os.path.isfile(resolver):
            p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                                "-File", resolver, "-CredPath", cred],
                               capture_output=True, text=True, encoding="utf-8", **_NOWINDOW)
            if p.returncode == 0:
                app_pw = (p.stdout or "").strip()
    if not app_pw:
        print("cannot resolve GMAIL_APP_PW; set it or pass --cred/--resolve-cred")
        return 2

    print("quality review: account=%s labels=%d sample=%d per label"
          % (a.account, len(labels), a.sample))

    all_findings, unreviewed = [], []
    for label in sorted(labels):
        pool = fetch_labelled(a.user, label, limit=0, app_pw=app_pw)
        if not pool:
            continue
        picked = [(f, s, label) for (f, s) in sample(pool, a.sample)]
        verdicts = judge(picked, cfg["taxonomy"])
        if verdicts is None:
            # An outage is not a clean result. Name it, and keep it out of the counts.
            unreviewed.append((label, len(picked)))
            print("  %-26s %3d sampled of %-4d -- REVIEWER UNREACHABLE, not reviewed"
                  % (label, len(picked), len(pool)))
            continue
        bad = [v for v in verdicts if v.get("wrong")]
        for v in bad:
            i = int(v.get("n", 0)) - 1
            if 0 <= i < len(picked):
                f, s, _ = picked[i]
                all_findings.append({"label": label, "from": f, "subject": s,
                                     "clause": v.get("clause", ""),
                                     "should_be": v.get("should_be", "")})
        print("  %-26s %3d sampled of %-4d -> %d wrong"
              % (label, len(picked), len(pool), len(bad)))

    print()
    if all_findings:
        print("%d finding(s):" % len(all_findings))
        for f in all_findings:
            print("  [%s] %s | %s" % (f["label"], f["from"][:32], f["subject"][:52]))
            print("      violates: %s" % f["clause"][:150])
            print("      should be: %s" % f["should_be"])
        # The lesson from 2026-09-01: the instance is the symptom. A sender that
        # appears repeatedly is a map defect, and fixing the map is what stops the
        # next backfill from recreating every instance.
        repeat = [(s, n) for s, n in collections.Counter(
            f["from"] for f in all_findings).most_common() if n > 1]
        if repeat:
            print()
            print("SENDERS APPEARING MORE THAN ONCE -- check rules/sender_map.json FIRST.")
            print("Fixing an instance without fixing the rule lets the next run recreate it.")
            for s, n in repeat:
                print("  %-44s %d findings" % (s[:44], n))
    else:
        print("no findings.")

    if unreviewed:
        print()
        print("NOT REVIEWED (reviewer unreachable) -- absence of findings here means nothing:")
        for label, n in unreviewed:
            print("  %-26s %d message(s)" % (label, n))

    if a.json:
        with open(os.path.expanduser(a.json), "w", encoding="utf-8") as fh:
            json.dump({"account": a.account, "findings": all_findings,
                       "unreviewed": [{"label": l, "sampled": n} for l, n in unreviewed]},
                      fh, ensure_ascii=False, indent=2)
        print("\nwrote %s" % a.json)

    # Exit codes carry the verdict, matching classification_review.py's convention so
    # the Task Scheduler's LastTaskResult means something.
    #   0 clean   3 findings to read   5 could not review at all
    if unreviewed and not all_findings:
        return 5
    return 3 if all_findings else 0


if __name__ == "__main__":
    sys.exit(main())
