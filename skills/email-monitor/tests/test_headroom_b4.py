#!/usr/bin/env python3
"""self-evolve headroom batch 4 — closing/opening boilerplate-hedge AI tells.

WHY THIS EXISTS (program-adjudication 0->1 headroom for signal #5):
A green baseline has no A-tier accept position, so headroom must be *added* as
failing tests that a real fix flips to passing (ARCHITECTURE 2.5 + signal #5).
Batch 2 extracted the rule engine into em_lint_rules.py (imports ONLY `re`),
unblocking the self-evolve patch import-gate. This batch lands fresh,
non-overlapping headroom on that same pure module.

THE REAL GAP (genuine value, not contrived):
The linter kills a few opening boilerplate phrases ("i hope this email finds you
well", "i wanted to reach out") but MISSES the much larger closing/opening
hedge-boilerplate family that is a classic LLM-customer-service tell:
  - "please do not hesitate to reach out" / "don't hesitate to contact"
  - "feel free to reach out / contact"
  - "looking forward to hearing from you"
  - "should you have any questions"
  - "i hope you are doing well" / "i hope all is well" / "hope this message finds you well"
  - "thank you for reaching out"
  - "i look forward to your response/reply"
  - "please let me know if you have any questions"
ARCHITECTURE 2.5 says opening/closing boilerplate should be deleted wholesale;
catching three openers while passing this entire hedge family is hardening
theater. Closing it is a real improvement to the deterministic AI-flavor gate,
orthogonal to b1 (rule-of-three / journey-roadmap), b2 (it's-worth-noting /
that-said throat-clearing) and b3 (sentence-initial transition adverbs).

A satisfying fix adds these as KILL_PHRASES substrings plus a few specific
BANNED_SHAPES regexes (pure re, gate-passing). The eleven headroom cases below
are distinct members of that family; the three guards ensure the new patterns
stay specific and do not over-match legitimate business / dealer language
("look forward to the test drive", bare "let me know", a clean OTD ask).

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

_HEADROOM_REASON = ("closing/opening boilerplate-hedge AI tell (signal #5); "
                    "satisfiable by KILL_PHRASES + specific BANNED_SHAPES "
                    "regexes; headroom for self-evolve A-tier 0->1.")


def _flagged(text, profile="business"):
    """True iff lint flags an AI-flavor shape/phrase/word violation."""
    viol = lr.lint(text, profile)
    return any(
        ("banned sentence shape" in v) or ("AI kill-list" in v)
        for v in viol
    )


def _draft(body):
    """Minimal compliant draft whose 2nd line carries the boilerplate hedge."""
    return ("Hi,\n"
            "%s\n"
            "Thanks,\n"
            "Daize Dong" % body)


# ── headroom (currently FAIL; the boilerplate-hedge fix flips all of them) ──

def test_hr01_do_not_hesitate_flagged():
    assert _flagged(_draft("Please do not hesitate to reach out.")), \
        "'please do not hesitate' is an AI hedge tell"


def test_hr02_dont_hesitate_contraction_flagged():
    assert _flagged(_draft("Please don't hesitate to contact me.")), \
        "contraction variant 'don't hesitate' must also be caught"


def test_hr03_feel_free_to_reach_out_flagged():
    assert _flagged(_draft("Please feel free to reach out with any questions.")), \
        "'feel free to reach out' is an AI boilerplate tell"


def test_hr04_looking_forward_to_hearing_flagged():
    assert _flagged(_draft("Looking forward to hearing from you.")), \
        "'looking forward to hearing from you' is an AI closing tell"


def test_hr05_should_you_have_any_questions_flagged():
    assert _flagged(_draft("Should you have any questions, let me know.")), \
        "'should you have any questions' is an AI boilerplate tell"


def test_hr06_i_hope_you_are_doing_well_flagged():
    assert _flagged(_draft("I hope you are doing well.")), \
        "'i hope you are doing well' is an AI opening tell"


def test_hr07_i_hope_all_is_well_flagged():
    assert _flagged(_draft("I hope all is well.")), \
        "'i hope all is well' is an AI opening tell"


def test_hr08_hope_this_message_finds_you_well_flagged():
    assert _flagged(_draft("Hope this message finds you well.")), \
        "'hope this message finds you well' variant must be caught"


def test_hr09_thank_you_for_reaching_out_flagged():
    assert _flagged(_draft("Thank you for reaching out.")), \
        "'thank you for reaching out' is an AI boilerplate tell"


def test_hr10_look_forward_to_your_response_flagged():
    assert _flagged(_draft("I look forward to your response.")), \
        "'i look forward to your response' is an AI closing tell"


def test_hr11_please_let_me_know_if_questions_flagged():
    assert _flagged(_draft("Please let me know if you have any questions.")), \
        "'please let me know if you have any questions' is an AI hedge tell"


# ── regression guards (currently PASS; the fix must NOT break them) ──

def test_g1_clean_dealer_ask_stays_clean():
    txt = ("Hi Sam,\n"
           "Please send your best out the door price today.\n"
           "Thanks,\n"
           "Daize Dong")
    assert not _flagged(txt), "a clean dealer ask must not trip the new hedge rules"


def test_g2_concrete_look_forward_stays_clean():
    txt = ("Hi,\n"
           "I look forward to the test drive on Saturday.\n"
           "Thanks,\n"
           "Daize Dong")
    assert not _flagged(txt), \
        "concrete 'look forward to <event>' is legitimate; only your-response/reply/hearing is a tell"


def test_g3_bare_let_me_know_stays_clean():
    txt = ("Hi,\n"
           "Let me know which trim you have in stock.\n"
           "Thanks,\n"
           "Daize Dong")
    assert not _flagged(txt), "bare 'let me know' is a legitimate CTA, not a hedge"
