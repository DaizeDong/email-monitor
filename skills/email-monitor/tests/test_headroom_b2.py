#!/usr/bin/env python3
"""self-evolve headroom batch 2 — AI-flavor meta-commentary / throat-clearing shapes.

WHY THIS EXISTS (program-adjudication 0->1 headroom for signal #5):
The batch-1 run proved a green baseline has no A-tier accept position (cold-start
before=0, no-regression gate untriggered), so headroom must be *added* as failing
tests that a real fix flips to passing (ARCHITECTURE 2.5 + signal #5).

Batch-1 headroom landed on em_draft_lint.py, which imports argparse/sys and is
therefore unpatchable by the self-evolve PATCH import-gate
(tools/sie/patch.py _DEFAULT_ALLOW = {json,math,re,typing,...}; no argparse/sys/os,
and statemachine passes no extra allow). Batch-2 applies that root-cause finding:
the rule engine was extracted into em_lint_rules.py (imports ONLY `re`), so the
proposer's fix surface now passes the gate.

THE REAL GAP (genuine value, not contrived):
The current linter bans the exact phrase "it is worth noting" but is trivially
evaded by the contraction ("it's worth noting") and by sibling meta-commentary
variants ("it is/it's important to note", "it's worth mentioning"), and it does
not catch the classic throat-clearing transition openers ("that said,",
"having said that,", "with that said,"). A linter that bans one spelling but
passes its contraction/variant is hardening theater. Closing this is a real
improvement to the deterministic AI-flavor gate.

A satisfying fix adds two regexes to em_lint_rules.BANNED_SHAPES (pure re,
gate-passing): one matching "it's/it is worth|important noting|mentioning|to
note|to mention", and one matching a line-start "that said,"/"having said
that,"/"with that said," throat-clearing transition opener.

Regression guards (G1/G2) ensure the fix does not over-match legitimate drafts.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import em_lint_rules as lr  # noqa: E402

# Run em-b2 (live, 3 rounds) reached DUAL_REVIEW every round but accepted 0:
# R1/R3 STATIC_REJECT@REVIEW (both judges reject the proposal), R2 STATIC_REJECT@PATCH.
# Option B did unblock the import-gate (em_lint_rules.py passes import_gate), but no
# proposer patch survived to land the fix. These headroom cases stay xfail(strict=False)
# so the regression gate is green while the self-evolve 0->1 semantics are preserved:
# grader records XFAIL=0.0 (gap open) and XPASS=1.0 the moment a real fix lands. The
# 2-regex fix is verified satisfiable (manually applied -> 34 passed, then reverted).
_HEADROOM_REASON = ("AI-flavor meta-commentary headroom (signal #5); satisfiable "
                    "(verified fix -> 34 passed); not yet auto-landed by self-evolve "
                    "(run em-b2: 0 accepted, all rounds STATIC_REJECT).")


def _flagged(text, profile="business"):
    """True iff lint flags an AI-flavor shape/phrase/word violation."""
    viol = lr.lint(text, profile)
    return any(
        ("banned sentence shape" in v)
        or ("AI kill-list" in v)
        for v in viol
    )


# ── headroom (currently FAIL; a real fix to em_lint_rules.BANNED_SHAPES flips them) ──

@pytest.mark.xfail(reason=_HEADROOM_REASON, strict=False)
def test_hr1_worth_noting_contraction_is_flagged():
    txt = ("Hi,\n"
           "It's worth noting that the offer expires Friday.\n"
           "Thanks,\n"
           "Daize Dong")
    assert _flagged(txt), "contraction 'it's worth noting' must be flagged like its full form"


@pytest.mark.xfail(reason=_HEADROOM_REASON, strict=False)
def test_hr2_important_to_note_variant_is_flagged():
    txt = ("Hi,\n"
           "It is important to note that the deposit is due Monday.\n"
           "Thanks,\n"
           "Daize Dong")
    assert _flagged(txt), "'it is important to note' is the same meta-commentary tell and must be flagged"


@pytest.mark.xfail(reason=_HEADROOM_REASON, strict=False)
def test_hr3_that_said_opener_is_flagged():
    txt = ("Hi,\n"
           "That said, I can sign this week.\n"
           "Thanks,\n"
           "Daize Dong")
    assert _flagged(txt), "throat-clearing transition opener 'That said,' must be flagged"


# ── regression guards (currently PASS; fix must NOT break them) ──

def test_g1_clean_dealer_draft_stays_clean():
    txt = ("Hi Sam,\n"
           "Please send your best out the door price today.\n"
           "Thanks,\n"
           "Daize Dong")
    assert not _flagged(txt), "a clean dealer ask must not trip the new shape rules"


def test_g2_legit_note_verb_stays_clean():
    txt = ("Hi,\n"
           "I noted your point about the timing and agree.\n"
           "Thanks,\n"
           "Daize Dong")
    assert not _flagged(txt), "bare verb 'noted' is legitimate and must not be flagged"
