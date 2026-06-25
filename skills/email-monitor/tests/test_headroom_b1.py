#!/usr/bin/env python3
"""email-monitor self-evolve batch-1 headroom (signal #5: AI-flavor banned shapes).

ARCHITECTURE.md §2.5 + signal #5 require the deterministic draft linter to flag, beyond
negation-parallel, two more AI tells that the current code does NOT implement:

  (a) rule-of-three parallelism  (三连排比): "A, B, and C" triples of short descriptors,
      a classic generated-prose cadence the linter must reject.
  (b) metaphor kill-words journey / roadmap (ARCHITECTURE §2.5 metaphor list explicitly
      names tapestry/landscape/realm/beacon/journey/roadmap; current KILL_WORDS omits the
      last two).

These cases FAIL on the current implementation (the rules do not exist yet) -> they are the
(0->1) headroom. Satisfying them must NOT regress the existing clean-draft contract, which the
guard tests below pin (rule-of-three detector must not fire on 2-item lists or CLEAN_DEALER).

All judgments are deterministic regex/scan output (no model self-assessment).
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)

import em_draft_lint as dl  # noqa: E402

# A draft that is clean under every CURRENT rule and trips ONLY rule-of-three.
RULE_OF_THREE = (
    "Hi Sam,\n\n"
    "Our service is fast, reliable, and affordable.\n\n"
    "Thanks,\nDaize Dong"
)

# Legitimate two-item list -- the new detector must leave this clean (no over-firing).
TWO_ITEM_OK = (
    "Hi Sam,\n\n"
    "Please send the price and the fees in one number.\n\n"
    "Thanks,\nDaize Dong"
)

# Reused clean dealer draft (must stay clean after the new rules land).
CLEAN_DEALER = (
    "Hi Sam,\n\n"
    "I am looking to buy a 2026 Honda Civic Sport and I am ready to move this week.\n\n"
    "Please send your best out-the-door price as one number: discounted selling price, "
    "minus rebates, plus all fees. I have my own financing, so quote price only.\n\n"
    "I am contacting a few dealers within 50 miles and will go with the cleanest quote. "
    "If you send a written breakdown today, I can commit fast.\n\n"
    "Thanks,\nDaize Dong"
)

JOURNEY = "Hi,\n\nLet us start this journey together.\n\nThanks,\nDaize Dong"
ROADMAP = "Hi,\n\nHere is our product roadmap for the year.\n\nThanks,\nDaize Dong"


# ---------- headroom (xfail until implemented) ----------
# These encode the (0->1) self-evolve headroom for batch 1. They are xfail (not plain fail)
# so the repo suite stays green, while preserving headroom semantics: self-evolve's grader
# scores XFAIL=0.0 (before) and XPASS=1.0 (after a proposer/human implements the rules), so a
# future round still sees a (0.0 -> 1.0) lift. The fix is verified-satisfiable (32/32 pass with
# journey/roadmap kill-words + a rule-of-three regex added to em_draft_lint), but the current
# self-evolve patch import-gate (tools/sie/patch.py _DEFAULT_ALLOW) rejects any rewrite of
# em_draft_lint.py because it imports argparse/sys (not in the allowlist). Landing is therefore
# deferred to human review (self-evolve iron law 5) or a patch-gate fix in a later batch.
HEADROOM_XFAIL = pytest.mark.xfail(
    reason="ARCHITECTURE 2.5 / signal #5 banned-shape gap; automated landing blocked by "
           "self-evolve patch import-gate (argparse/sys not allowlisted). Verified satisfiable.",
    strict=False,
)


@HEADROOM_XFAIL
def test_rule_of_three_is_flagged():
    viol = dl.lint(RULE_OF_THREE, "business")
    assert viol, "rule-of-three draft must be rejected"
    joined = " ".join(viol).lower()
    assert ("three" in joined) or ("parallel" in joined) or ("rule of three" in joined), (
        "expected a rule-of-three / parallelism violation, got: %r" % viol
    )


@HEADROOM_XFAIL
def test_journey_metaphor_killed():
    viol = dl.lint(JOURNEY, "business")
    assert any("kill-list" in v for v in viol), (
        "journey metaphor must be caught by the kill-list, got: %r" % viol
    )


@HEADROOM_XFAIL
def test_roadmap_metaphor_killed():
    viol = dl.lint(ROADMAP, "business")
    assert any("kill-list" in v for v in viol), (
        "roadmap metaphor must be caught by the kill-list, got: %r" % viol
    )


# ---------- regression guards (pass now; the new rules must not break them) ----------

def test_two_item_list_stays_clean():
    assert dl.lint(TWO_ITEM_OK, "business") == [], (
        "two-item list must not trip the rule-of-three detector"
    )


def test_clean_dealer_unaffected_by_new_rules():
    assert dl.lint(CLEAN_DEALER, "dealer") == [], (
        "CLEAN_DEALER must remain clean after the new banned-shape rules land"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
