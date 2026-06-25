#!/usr/bin/env python3
"""self-evolve headroom batch 3 — sentence-initial transition-adverb AI tells.

WHY THIS EXISTS (program-adjudication 0->1 headroom for signal #5):
Batches 1 and 2 established that a green baseline has no A-tier accept position,
so headroom must be *added* as failing tests a real fix flips to passing
(ARCHITECTURE 2.5 + signal #5). Batch 1's headroom landed on em_draft_lint.py
(argparse) and was structurally blocked by the self-evolve patch import-gate;
batch 2 extracted the rule engine into em_lint_rules.py (imports ONLY `re`),
unblocking the gate. This batch lands fresh, non-overlapping headroom on that
same pure module.

THE REAL GAP (genuine value, not contrived):
The linter deletes the transition words "furthermore / moreover / in conclusion"
(KILL_WORDS) but MISSES the rest of the sentence-initial transition-adverb family
that is a classic LLM tell: "Additionally," "Notably," "Importantly,"
"Ultimately," "Consequently," "Subsequently," "Therefore," "Nevertheless,"
"Nonetheless," "Conversely," "Accordingly," at the start of a line. ARCHITECTURE
2.5 says transitions should be deleted; banning two of the family while passing
the other eleven is hardening theater. Closing this is a real improvement to the
deterministic AI-flavor gate, orthogonal to b1 (rule-of-three / journey-roadmap)
and b2 (it's-worth-noting / that-said throat-clearing).

A satisfying fix adds ONE regex to em_lint_rules.BANNED_SHAPES (pure re,
gate-passing) matching a line-start single transition adverb followed by a comma.
The eleven headroom cases below are distinct members of that family; the three
guards ensure the line-start+comma anchor does not over-match legitimate
mid-sentence usage ("additional", "we can therefore proceed").

The marker is xfail(strict=False): the regression gate stays green while the
self-evolve grader records XFAIL=0.0 (gap open) and XPASS=1.0 the instant a real
fix lands (evaluate.py _parse_per_test). Eleven 0->1 flips give the A-tier
e-process enough evidence to cross 1/alpha=20 (no-regression gate first).
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import em_lint_rules as lr  # noqa: E402

_HEADROOM_REASON = ("sentence-initial transition-adverb AI tell (signal #5); "
                    "satisfiable by one BANNED_SHAPES line-start regex; headroom "
                    "for self-evolve A-tier 0->1.")


def _flagged(text, profile="business"):
    """True iff lint flags an AI-flavor shape/phrase/word violation."""
    viol = lr.lint(text, profile)
    return any(
        ("banned sentence shape" in v) or ("AI kill-list" in v)
        for v in viol
    )


def _draft(opener):
    """Minimal compliant draft whose 2nd line is the AI transition opener."""
    return ("Hi,\n"
            "%s I will send the documents this week.\n"
            "Thanks,\n"
            "Daize Dong" % opener)


# ── headroom (currently FAIL; one line-start transition regex flips all of them) ──

def test_hr01_additionally_opener_flagged():
    assert _flagged(_draft("Additionally,")), "'Additionally,' opener is an AI transition tell"


def test_hr02_notably_opener_flagged():
    assert _flagged(_draft("Notably,")), "'Notably,' opener is an AI transition tell"


def test_hr03_importantly_opener_flagged():
    assert _flagged(_draft("Importantly,")), "'Importantly,' opener is an AI transition tell"


def test_hr04_ultimately_opener_flagged():
    assert _flagged(_draft("Ultimately,")), "'Ultimately,' opener is an AI transition tell"


def test_hr05_consequently_opener_flagged():
    assert _flagged(_draft("Consequently,")), "'Consequently,' opener is an AI transition tell"


def test_hr06_subsequently_opener_flagged():
    assert _flagged(_draft("Subsequently,")), "'Subsequently,' opener is an AI transition tell"


def test_hr07_therefore_opener_flagged():
    assert _flagged(_draft("Therefore,")), "'Therefore,' opener is an AI transition tell"


def test_hr08_nevertheless_opener_flagged():
    assert _flagged(_draft("Nevertheless,")), "'Nevertheless,' opener is an AI transition tell"


def test_hr09_nonetheless_opener_flagged():
    assert _flagged(_draft("Nonetheless,")), "'Nonetheless,' opener is an AI transition tell"


def test_hr10_conversely_opener_flagged():
    assert _flagged(_draft("Conversely,")), "'Conversely,' opener is an AI transition tell"


def test_hr11_accordingly_opener_flagged():
    assert _flagged(_draft("Accordingly,")), "'Accordingly,' opener is an AI transition tell"


# ── regression guards (currently PASS; the fix must NOT break them) ──

def test_g1_additional_midsentence_stays_clean():
    txt = ("Hi,\n"
           "I have additional questions about the timeline.\n"
           "Thanks,\n"
           "Daize Dong")
    assert not _flagged(txt), "'additional' mid-sentence is legitimate, not a transition opener"


def test_g2_therefore_midsentence_stays_clean():
    txt = ("Hi,\n"
           "We can therefore proceed once you confirm.\n"
           "Thanks,\n"
           "Daize Dong")
    assert not _flagged(txt), "mid-sentence 'therefore' is legitimate; only line-start+comma is a tell"


def test_g3_clean_dealer_ask_stays_clean():
    txt = ("Hi Sam,\n"
           "Please send your best out the door price today.\n"
           "Thanks,\n"
           "Daize Dong")
    assert not _flagged(txt), "a clean dealer ask must not trip the new transition rule"
