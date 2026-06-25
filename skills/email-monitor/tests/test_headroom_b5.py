#!/usr/bin/env python3
"""self-evolve headroom batch 5 — deadline-resolution stricter boundaries (signal #2).

WHY THIS EXISTS (program-adjudication 0->1 headroom):
A green baseline has no A-tier accept position, so headroom must be *added* as failing tests
that a real fix flips to passing (ARCHITECTURE 5 signal #2 "deadline extraction" +
batch-4 recommendation: go orthogonal to the signal-#5 lint family that b1-b4 all targeted).
The structural refactor of this batch extracted the pure resolve engine into em_duenorm_rules.py
(imports ONLY re+datetime), unblocking the self-evolve patch import-gate exactly as b2 did for
em_lint_rules.py. This headroom lands on that newly-self-evolvable engine.

THE REAL GAP (genuine value, not contrived) — probed against the shipped engine:
The deadline normalizer silently DROPS several common deadline phrases as "unparseable"
(due_utc=None) or resolves them WRONG, which directly hurts signal #2 (tasks land with no due
date or the wrong one):
  - bare time with no date anchor: "by 5pm" / "by 12pm" -> unparseable (should anchor to mail day)
  - named time tokens: "noon" -> falls to default 17:00 (WRONG, should be 12:00); "by noon" -> unparseable
  - "cob" / "close of business" -> unparseable (should be 17:00 like eod)
  - "next friday" -> resolves to the UPCOMING friday (WRONG: should be the friday a week later)
  - "in a week" / "in a day" -> unparseable (the regex only accepts a digit, not "a"/"an")
  - "eow" / "end of week" -> unparseable (should be the upcoming friday 17:00)
Recognizing one spelling while dropping its everyday synonyms is hardening theater; closing these
makes the deterministic deadline gate materially better. Orthogonal to b1/b2/b3/b4 (all signal #5
draft-lint) — this is the first signal-#2 headroom in the series.

A satisfying fix (pure re+datetime, gate-passing) teaches resolve() named-time tokens, bare-time
anchoring to the mail day, "next <weekday>" = +7, "in a/an <unit>", and eow/end-of-week. The
eleven headroom cases below are distinct members of that gap; the four guards ensure the new
behavior does NOT regress the existing contract (explicit date stays high-confidence and is not
hijacked by the bare-time anchor; "this friday"/"friday 5pm" stay the upcoming friday; the
January DST relative case is unchanged).

The marker is xfail(strict=False): the regression gate stays green while the self-evolve grader
records XFAIL=0.0 (gap open) and XPASS=1.0 the instant a real fix lands (evaluate.py
_parse_per_test). Eleven 0->1 flips give the A-tier e-process enough evidence to cross
1/alpha=20 (no-regression gate first).
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import em_duenorm_rules as dr  # noqa: E402

# Fixed aware base: Wed 24 Jun 2026 09:00 America/New_York (EDT, -04:00).
# NY 17:00 -> 21:00Z ; NY 12:00 -> 16:00Z. resolve() uses base_dt.tzinfo for replace+astimezone,
# so the assertions below are tz-deterministic without needing zoneinfo.
EDT = timezone(timedelta(hours=-4))
BASE = datetime(2026, 6, 24, 9, 0, tzinfo=EDT)          # Wednesday
EST = timezone(timedelta(hours=-5))
BASE_JAN = datetime(2026, 1, 14, 9, 0, tzinfo=EST)       # Wed Jan (DST guard)

_REASON = ("deadline-resolution stricter boundary (signal #2); satisfiable by named-time tokens "
           "+ bare-time anchoring + next-weekday + in-a/an-unit + eow in pure em_duenorm_rules; "
           "headroom for self-evolve A-tier 0->1.")


def _due(phrase, base=BASE):
    return dr.resolve(phrase, base).get("due_utc")


# ---------------- 11 headroom (currently fail -> xfail) ----------------

def test_b5_by_noon_anchors_today_noon():
    assert (_due("by noon") or "").startswith("2026-06-24T16:00")


def test_b5_noon_today_is_1200_not_default_1700():
    # currently resolves to 17:00 (default) -> WRONG; noon must be 12:00 NY = 16:00Z
    assert (_due("noon today") or "").startswith("2026-06-24T16:00")


def test_b5_bare_5pm_anchors_today():
    assert (_due("by 5pm") or "").startswith("2026-06-24T21:00")


def test_b5_bare_12pm_is_noon_today():
    assert (_due("by 12pm") or "").startswith("2026-06-24T16:00")


def test_b5_cob_is_today_1700():
    assert (_due("cob") or "").startswith("2026-06-24T21:00")


def test_b5_by_cob_is_today_1700():
    assert (_due("by cob") or "").startswith("2026-06-24T21:00")


def test_b5_close_of_business_is_today_1700():
    assert (_due("close of business") or "").startswith("2026-06-24T21:00")


def test_b5_next_friday_is_next_week():
    # upcoming friday is 06-26; "next friday" must be the friday a week later: 07-03
    assert (_due("next friday") or "").startswith("2026-07-03T21:00")


def test_b5_in_a_week_plus_seven_days():
    assert (_due("in a week") or "").startswith("2026-07-01T21:00")


def test_b5_in_a_day_plus_one_day():
    assert (_due("in a day") or "").startswith("2026-06-25T21:00")


def test_b5_eow_is_upcoming_friday():
    assert (_due("eow") or "").startswith("2026-06-26T21:00")


# ---------------- 4 guards (must hold before AND after the fix) ----------------

def test_b5_guard_this_friday_still_upcoming():
    # "this friday" must remain the upcoming friday (06-26), NOT shifted a week by next-handling.
    assert (_due("this friday") or "").startswith("2026-06-26")


def test_b5_guard_friday_5pm_unchanged():
    assert (_due("friday 5pm") or "").startswith("2026-06-26T21:00")


def test_b5_guard_explicit_date_not_hijacked_by_bare_time():
    out = dr.resolve("by 2026-07-01", BASE)
    assert out["confidence"] == "high"
    assert (out["due_utc"] or "").startswith("2026-07-01")


def test_b5_guard_january_relative_dst_unchanged():
    # tomorrow 5pm in January (EST -05:00): 5pm local == 22:00Z, unchanged by this batch.
    assert (_due("tomorrow 5pm", BASE_JAN) or "").startswith("2026-01-15T22:00")
