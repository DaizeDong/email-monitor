#!/usr/bin/env python3
"""make_fixtures -- the test fixtures are GENERATED, because a real record cannot be regenerated.

WHY THIS EXISTS
---------------
The 2026-07 audit found the operator's real private data in this repo, and the vector was a test
fixture: `golden_classify.jsonl` had been built by pasting REAL emails out of the inbox the skill was
reading -- real senders, a real person's name, a real employer. It was scrubbed and the history was
rewritten, but scrubbing is not a fix, because nothing stopped the next agent from doing it again.

And the next agent WILL be tempted, because the temptation is structural, not careless. An agent
writing a classifier test needs a realistic message; it is already holding a mailbox full of real
ones; copy-paste is the cheapest move available. Every leak in the audit started exactly there.

So the fixture is not allowed to be a file that a human (or an agent) edits. It is OUTPUT. The only
input is the CASE TABLE below -- a list of behaviours the classifier must exhibit -- and every
address, subject and flag in it is synthetic by construction.

That is the entire trick, and it is worth stating plainly:

    A REAL EMAIL CANNOT BE REGENERATED.

`tools/data_boundary.py` re-runs this generator and requires the committed .jsonl to be byte-identical
to what comes out. So if someone pastes a real message into the golden file, the file no longer
matches its generator and the check fails LOUDLY, at commit time -- instead of the leak being noticed
months later, in an audit, or never. A content scanner asks "does this look private?", which fails on
anything it has not been taught. This asks "could this have been produced from the case table?",
which a real record can never satisfy, no matter how innocuous it looks.

WORKFLOW
--------
    edit CASES below  ->  python tools/make_fixtures.py  ->  commit the .py and the .jsonl together

You never hand-edit `skills/email-monitor/tests/golden_classify.jsonl`. If you want a new behaviour
pinned, you add a CASE -- which forces you to say, in words, WHICH classifier path you are pinning and
WHY, and forces the message itself to be invented rather than borrowed.

    python tools/make_fixtures.py              regenerate in place
    python tools/make_fixtures.py --out DIR    write to DIR (used by data_boundary.py)

Stdlib only. Deterministic: no clock, no randomness, no environment. Same table -> same bytes.
"""
import argparse
import json
import os
import sys

# Every message in the golden set belongs to this synthetic account. It is not a real mailbox, and it
# is not a slug from the operator's registry -- the registry lives in the private companion config and
# is deliberately out of this repo's reach (see tools/datadir.py).
ACCOUNT = "user1"

# ---------------------------------------------------------------------------------------------
# THE CASE TABLE -- the only hand-written thing here, and the only thing you may change.
#
# Each case pins ONE classifier behaviour: a signal goes in, a priority must come out. The `path`
# field names the branch of em_classify.py that is under test, so that a case cannot be added without
# saying what it is for -- and so a future refactor that deletes a branch has a named test to answer.
#
# HARD RULE: every value below is INVENTED. Synthetic namespace only -- `example.com`,
# `example-<role>.com`. Never a real vendor, person, employer, or address, not even one that seems
# harmless. "It was only a store's marketing address" is how the last leak was rationalized.
#
# Priorities: URGENT | ACTION | FYI | NOISE. Safe-fail is FYI (park it), never NOISE (silent swallow).
# ---------------------------------------------------------------------------------------------
CASES = [
    {
        "path": "L0 vip -> ACTION",
        "why": "a sender on the personal VIP layer is always actionable, whatever the subject says",
        "frm": "recruiter@example-employer.com",
        "subject": "Re: start date confirmation",
        "expect": "ACTION",
        "note": "VIP sender via personal layer",
    },
    {
        "path": "L0 urgent_keyword -> URGENT",
        "why": "an urgency word in the subject is high-confidence enough to skip scoring entirely",
        "frm": "billing@example-utility.com",
        "subject": "URGENT: payment failed, account will be suspended",
        "expect": "URGENT",
        "note": "urgent keyword in subject",
    },
    {
        "path": "L1 low_score+marketing -> NOISE",
        "why": "marketing label plus a dead score is the one case allowed to bypass the FYI safe-fail",
        "frm": "promo@example-shoes.com",
        "subject": "50% off sale ends tonight, shop now",
        "list_unsubscribe": True,
        "expect": "NOISE",
        "note": "marketing + list-unsubscribe + noreply pattern",
    },
    {
        "path": "L0 list_unsub+noreply -> NOISE",
        "why": "List-Unsubscribe AND a no-reply sender: bulk by construction, no human waiting",
        "frm": "no-reply@example-social.com",
        "subject": "Someone liked your photo",
        "list_unsubscribe": True,
        "expect": "NOISE",
        "note": "social noreply list-unsub",
    },
    {
        "path": "L0 vip -> ACTION (with action verb)",
        "why": "VIP fires at L0 before scoring; pins that the deadline in the subject cannot demote it",
        "frm": "leasing@example-property.com",
        "subject": "Please sign the renewal by Friday",
        "expect": "ACTION",
        "note": "VIP + action verb",
    },
    {
        # KNOWN MISS -- LEAVE IT MISSING. The classifier answers FYI here; this case demands NOISE.
        # A List-Unsubscribe newsletter from a non-noreply sender slips past the L0 bulk rule, and at
        # L1 its subject carries no marketing word ("Your weekly digest"), so it labels as
        # `notification` and safe-fails to FYI. That is the single failure in the golden set: accuracy
        # is 7/8 = 0.875 against the >= 0.85 gate in test_acceptance.py.
        #
        # This case is the standing pressure on that gap. Do NOT "fix" it by relaxing `expect` to FYI
        # to make the suite greener -- that deletes the only record that the gap exists and buys
        # exactly nothing. Fix it, if you fix it, in em_classify.py (L0 should weigh List-Unsubscribe
        # without requiring a no-reply sender), and then this case starts passing on its own.
        "path": "L1 newsletter -> NOISE (currently FYI: known classifier gap)",
        "why": "bulk newsletters should be NOISE even when the sender is not a no-reply address",
        "frm": "newsletter@example-blog.com",
        "subject": "Your weekly digest",
        "list_unsubscribe": True,
        "expect": "NOISE",
        "note": "newsletter marketing",
    },
    {
        "path": "L1 low_score_safe_fyi -> FYI",
        "why": "an unknown human with no signal must PARK (FYI), never be swallowed (NOISE)",
        "frm": "colleague@example-employer.com",
        "subject": "quick question about the deck",
        "expect": "FYI",
        "note": "unknown sender, no strong signal -> safe FYI",
    },
    {
        "path": "L1 calendar -> FYI",
        "why": "a calendar label alone is not an obligation; it parks rather than escalating",
        "frm": "calendar@example-meetings.com",
        "subject": "Meeting invite: project sync tomorrow",
        "expect": "FYI",
        "note": "calendar notification, unknown sender",
    },
]

FIXTURE = os.path.join("skills", "email-monitor", "tests", "golden_classify.jsonl")


def build_row(case):
    """One CASE -> one golden row. The msg keys are inserted in a FIXED order on purpose.

    Do not "tidy" this into json.dumps(sort_keys=True): the committed fixture is written in this
    order, and data_boundary.py compares bytes, so reordering keys would look exactly like a
    hand-edited (i.e. possibly real) fixture. Insertion order is deterministic in Python (>=3.7),
    which is all determinism requires here.
    """
    msg = {"from": case["frm"], "subject": case["subject"]}
    if case.get("list_unsubscribe"):
        msg["list_unsubscribe"] = True
    msg["account"] = ACCOUNT
    return {"msg": msg, "expect_priority": case["expect"], "note": case["note"]}


def render():
    """The whole fixture as one string. UTF-8, LF, trailing newline, no BOM."""
    return "".join(
        json.dumps(build_row(c), ensure_ascii=False, sort_keys=False) + "\n" for c in CASES)


def repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser(description="Generate the synthetic test fixtures.")
    ap.add_argument("--out", help="write fixtures into this directory (default: regenerate in place)")
    a = ap.parse_args()

    if a.out:
        os.makedirs(a.out, exist_ok=True)
        dest = os.path.join(a.out, os.path.basename(FIXTURE))
    else:
        dest = os.path.join(repo_root(), FIXTURE)
        os.makedirs(os.path.dirname(dest), exist_ok=True)

    # newline="\n": never let Windows translate this to CRLF -- the bytes are the contract.
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        f.write(render())
    print("make_fixtures: wrote %d case(s) -> %s" % (len(CASES), dest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
