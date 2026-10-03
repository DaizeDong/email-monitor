#!/usr/bin/env python3
"""Adversarial sample review of applied labels. Never modifies mailbox labels.

Operational counters measure whether classification ran. This reviewer instead
asks whether each sampled label violates the configured taxonomy. It requires a
quoted clause before reporting a defect and defaults to no finding when the
available headers support the label.

Review is manual and opt-in through quality_review.enabled. Findings can require
a sender-map or taxonomy change; removing a label without correcting its rule can
recreate the same error. Disabled review and a review with no findings are
reported separately. Reports and sampled headers belong in a verified PRIVATE
companion, under version control. --force overrides only the enabled switch;
PRIVATE paths and the installed model policy are always checked before mail access.

  python em_quality_review.py --account <slug> --user <addr>
  python em_quality_review.py --account <slug> --user <addr> --json <private-path>
"""
from __future__ import annotations

import argparse
import collections
import json
import os
from pathlib import Path
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import em_topic  # noqa: E402
import em_runtime  # noqa: E402
import em_watch  # noqa: E402
from em_runtime import reject_model_overrides  # noqa: E402

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
- The same cut forbids the opposite move. When the standard requires evidence that normally
  lives in the BODY -- an amount, an order total, the words receipt or invoice -- its absence
  from the subject line is NOT evidence that it is absent from the message. You cannot see the
  body, so you cannot find that kind of violation at all. Say nothing rather than infer it. A
  card notification whose subject is only the merchant name still carries the amount inside.
%(allowed)s
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

    Exclude messages from the account's own address: the review should sample
    incoming messages judged by the kernel. Thread-level operations can attach
    labels to sent replies, but those labels do not establish a kernel decision.
    Exclusion changes the audit population; it does not remove mailbox labels.
    """
    args = [sys.executable, LABEL_TOOL, "--user", user,
            "--query", 'label:"%s" -from:%s' % (label, user), "--add", label, "--dry"]
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
    try:
        p = run(args, env)
    except (OSError, subprocess.SubprocessError):
        return None
    if getattr(p, "returncode", 1) != 0:
        return None
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


def _allowed_block(allowed):
    """The labels this ACCOUNT actually has, which is not the same as the taxonomy's.

    A proposal outside this set is worse than no proposal: `em_topic.judge` drops a
    mapped label that is not in the account's allowed set. A repair must not remove
    an existing label in favor of a replacement the account cannot use.
    """
    if not allowed:
        return ""
    parts = ["- `should_be` MUST name one of the labels THIS ACCOUNT HAS, or be empty to",
             "  mean the label should simply be removed. The account has exactly these:"]
    parts += ["    %s" % l for l in allowed]
    parts += ["  A label absent from that list does not exist here. Proposing one is worse",
              "  than proposing nothing: acting on it strips the message and adds nothing."]
    return "\n".join(parts) + "\n"


def _validated_findings(verdicts, count, taxonomy, allowed):
    """Require one well-formed decision per sample before reporting a clean review."""
    if not isinstance(verdicts, list) or len(verdicts) != count:
        return None
    seen = set()
    findings = []
    for row in verdicts:
        if (not isinstance(row, dict) or type(row.get('n')) is not int
                or not 1 <= row['n'] <= count or row['n'] in seen
                or type(row.get('wrong')) is not bool
                or not isinstance(row.get('clause'), str)
                or not isinstance(row.get('should_be'), str)):
            return None
        seen.add(row['n'])
        if row['wrong']:
            if (not row['clause'].strip() or row['clause'] not in taxonomy
                    or (allowed and row['should_be'] and row['should_be'] not in allowed)):
                return None
            findings.append(row)
        elif row['clause']:
            return None
    return findings


def judge(items, taxonomy, allowed=None, call=None, timeout=None):
    """Refute a batch; None means unavailable/invalid review, [] means no findings.

    Model policy belongs to installed llmcall. A legacy timeout is an error.
    """
    reject_model_overrides(timeout=timeout)
    if not items:
        return []
    listing = "\n".join(
        "%d. FROM: %s\n   SUBJECT: %s\n   LABEL: %s" % (i + 1, f, s, l)
        for i, (f, s, l) in enumerate(items))
    prompt = PROMPT % {"taxonomy": taxonomy, "items": listing,
                       "allowed": _allowed_block(allowed)}
    try:
        if call is not None:
            verdicts = call(prompt)
        else:
            if llmcall is None:
                return None
            result = llmcall.call(prompt, mode="judge", schema=VERDICT_SCHEMA)
            data = getattr(result, 'data', None) if result else None
            verdicts = data.get('findings') if isinstance(data, dict) else None
    except Exception:
        # The caller records this batch as unreviewed; transport errors never become clean results.
        return None
    return _validated_findings(verdicts, len(items), taxonomy, allowed)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--account", required=True)
    ap.add_argument("--user", required=True)
    ap.add_argument("--labels", default=None,
                    help="comma-separated subset; default is every allowed label")
    ap.add_argument("--sample", type=int, default=DEFAULT_SAMPLE,
                    help="messages sampled per label (default %d)" % DEFAULT_SAMPLE)
    ap.add_argument("--json", default=None, help="write findings inside a verified PRIVATE companion")
    ap.add_argument("--registry", default="~/.email-monitor-config/registry.json")
    ap.add_argument("--force", action="store_true",
                    help="run even when quality_review.enabled is false")
    ap.add_argument("--cred", default=None)
    ap.add_argument("--resolve-cred",
                    default=os.path.join("~", ".email-monitor-config", "scripts", "resolve-cred.ps1"))
    a = ap.parse_args(argv)

    if a.sample <= 0:
        ap.error('--sample must be positive')
    registry = Path(a.registry).expanduser()
    if not registry.is_file() and not a.force:
        print("quality_review is DISABLED in registry.json -- nothing was reviewed.")
        return 0

    try:
        registry = Path(em_runtime.prove_private(registry)['path'])
        output = None
        if a.json:
            output = em_runtime.prove_private(a.json)['path']
        with registry.open(encoding='utf-8-sig') as handle:
            settings = json.load(handle)
        if not isinstance(settings, dict):
            raise ValueError('registry must be an object')
        runtime = em_runtime.runtime_config(settings, registry.parent)
        enabled = bool((settings.get('quality_review') or {}).get('enabled', False))
        if not enabled and not a.force:
            print("quality_review is DISABLED in registry.json -- nothing was reviewed.")
            print("Set quality_review.enabled true, or pass --force for a one-off run.")
            return 0
        # This command always judges with a model, irrespective of the heartbeat's classifier mode.
        em_runtime.check_local_route(runtime, {'mode': 'agent'}, False)
        if not em_runtime.valid_account_slug(a.account):
            raise ValueError('invalid account slug')
        if llmcall is None or not callable(getattr(llmcall, 'call', None)):
            print('llmcall dependency unavailable; nothing was reviewed.', file=sys.stderr)
            return 5
        for name in ('taxonomy.md', 'sender_map.json', 'labels.json'):
            em_runtime.prove_private(registry.parent/'rules'/name)
    except (OSError, ValueError, RuntimeError) as exc:
        print('quality review preflight failed: '+str(exc), file=sys.stderr)
        return 1

    cfg = em_topic.load_config(a.account, config_dir=str(registry.parent), log=lambda m: print(m))
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
    sampled_total = 0
    for label in sorted(labels):
        pool = fetch_labelled(a.user, label, limit=0, app_pw=app_pw)
        if pool is None:
            unreviewed.append({'label': label, 'sampled': 0, 'reason': 'fetch_failed'})
            print("  %-26s FETCH FAILED, not reviewed" % label)
            continue
        if not pool:
            continue
        picked = [(f, s, label) for (f, s) in sample(pool, a.sample)]
        sampled_total += len(picked)
        verdicts = judge(picked, cfg["taxonomy"], cfg["allowed_labels"])
        if verdicts is None:
            # An outage is not a clean result. Name it, and keep it out of the counts.
            unreviewed.append({'label': label, 'sampled': len(picked), 'reason': 'review_unavailable'})
            print("  %-26s %3d sampled of %-4d -- REVIEW UNAVAILABLE, not reviewed"
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
        # Repeated sender findings can indicate a map defect. Check the rule
        # before changing individual labels so later runs do not recreate them.
        repeat = [(s, n) for s, n in collections.Counter(
            f["from"] for f in all_findings).most_common() if n > 1]
        if repeat:
            print()
            print("SENDERS APPEARING MORE THAN ONCE -- check rules/sender_map.json FIRST.")
            print("Fixing an instance without fixing the rule lets the next run recreate it.")
            for s, n in repeat:
                print("  %-44s %d findings" % (s[:44], n))
    elif unreviewed:
        print("no confirmed findings; some labels were not reviewed.")
    elif not sampled_total:
        print("no messages sampled; nothing reviewed.")
    else:
        print("no findings.")

    if unreviewed:
        print()
        print("NOT REVIEWED -- absence of findings here means nothing:")
        for row in unreviewed:
            print("  %-26s %d message(s): %s" % (row['label'], row['sampled'], row['reason']))

    if output:
        try:
            em_watch.save_state(output, {'account': a.account, 'findings': all_findings,
                                        'unreviewed': unreviewed, 'sampled': sampled_total})
        except (OSError, ValueError, RuntimeError) as exc:
            print('quality report write failed: '+str(exc), file=sys.stderr)
            return 1
        print("\nwrote %s" % output)

    # Exit codes carry the verdict, matching classification_review.py's convention so
    # the Task Scheduler's LastTaskResult means something.
    #   0 clean   3 findings to read   5 could not review at all
    if unreviewed and not all_findings:
        return 5
    return 3 if all_findings else 0


if __name__ == "__main__":
    sys.exit(main())
