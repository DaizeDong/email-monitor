#!/usr/bin/env python3
"""self-evolve headroom batch 7 — named-month deadline resolution (signal #2).

WHY THIS EXISTS (program-adjudication 0->1 headroom):
A green baseline has no A-tier accept position, so headroom must be *added* as failing tests
that a real fix flips to passing. Batches 5-6 hardened the pure engine em_duenorm_rules.py
(imports ONLY re+datetime, so it passes the self-evolve patch import-gate). This batch lands
MORE signal-#2 headroom on the same self-evolvable engine.

THE REAL GAP (genuine value, not contrived) — probed against the shipped engine:
The deadline normalizer silently DROPS every spelled-out calendar date. Real mail constantly
says "by July 4", "January 5", "by Aug 15", "due Dec 1" -- and resolve() returns
due_utc=None (unparseable) for all of them, so the affair lands in the pool with NO deadline.
The engine already handles ISO (2026-07-01) and m/d (7/4) numeric dates at high confidence, so
recognizing the numeric spelling while dropping the everyday English spelling is exactly the
"hardening theater" the philosophy forbids. Closing it makes the deterministic deadline gate
materially better and is orthogonal to b5 (named-time/bare-time/next-weekday/eow), b6
(end-of-month/business-days/this-weekend/asap/within/next-week/ordinal/day-parts), and b1-b4
(all signal #5 draft-lint).

A satisfying fix (pure re+datetime, gate-passing) teaches resolve() to parse a month NAME
(full or 3/4-letter abbrev) plus a day-of-month, in either order ("July 4" / "4 July"), with an
optional explicit year (high confidence) and otherwise a next-occurrence year-rollover when the
date has already passed this year (med confidence). The eleven headroom cases below are distinct
members of that gap; the five guards ensure the new behavior does NOT regress the existing
contract: numeric ISO dates stay high-confidence and are not hijacked, a lone month name with no
day stays unparseable (no guessed day), the modal word "may" is not mistaken for the month May,
"in 3 days" stays a calendar offset, and bare "noon" stays 12:00.

The marker is xfail(strict=False): the regression gate stays green while the self-evolve grader
records XFAIL=0.0 (gap open) and XPASS=1.0 the instant a real fix lands. Eleven 0->1 flips give
the A-tier e-process enough evidence to cross 1/alpha=20 (no-regression gate first).
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import em_duenorm_rules as dr  # noqa: E402

# Fixed aware base: Wed 24 Jun 2026 09:00 America/New_York (fixed -04:00) -- same as b5/b6.
# The pure engine carries this fixed offset through datetime.replace(), so EVERY resolved date
# stays at -04:00 in this unit test (real DST is the outer em_duenorm.py shell's job, covered by
# test_acceptance.test_tz_dst_no_offset_error). Thus 17:00 -> 21:00Z and 14:00 -> 18:00Z here.
EDT = timezone(timedelta(hours=-4))
BASE = datetime(2026, 6, 24, 9, 0, tzinfo=EDT)          # Wednesday, month 6, day 24

_REASON = ("named-month deadline resolution (signal #2); satisfiable by a pure-re+datetime "
           "month-name+day rule (either order, optional year, next-occurrence rollover) in "
           "em_duenorm_rules; headroom for self-evolve A-tier 0->1.")

HEADROOM_XFAIL = pytest.mark.xfail(reason=_REASON, strict=False)


def _due(phrase, base=BASE):
    return dr.resolve(phrase, base).get("due_utc")


def _conf(phrase, base=BASE):
    return dr.resolve(phrase, base).get("confidence")


# ---------------- 11 headroom (currently fail -> xfail) ----------------

@HEADROOM_XFAIL
def test_b7_month_name_future_this_year():
    # "july 4": July is after June -> this year, 17:00 -> 21:00Z
    assert (_due("by july 4") or "").startswith("2026-07-04T21:00")


@HEADROOM_XFAIL
def test_b7_month_name_rolls_to_next_year_when_passed():
    # "january 5": Jan < June -> already passed this year -> 2027
    assert (_due("january 5") or "").startswith("2027-01-05T21:00")


@HEADROOM_XFAIL
def test_b7_month_name_with_by_prefix_august():
    assert (_due("by august 15") or "").startswith("2026-08-15T21:00")


@HEADROOM_XFAIL
def test_b7_month_name_explicit_year_is_high_conf():
    assert (_due("jan 5 2027") or "").startswith("2027-01-05T21:00")
    assert _conf("jan 5 2027") == "high"


@HEADROOM_XFAIL
def test_b7_month_name_december():
    assert (_due("december 1") or "").startswith("2026-12-01T21:00")


@HEADROOM_XFAIL
def test_b7_month_abbrev_feb_rolls_next_year():
    # "feb 28": Feb < June -> 2027; Feb 2027 has 28 days
    assert (_due("by feb 28") or "").startswith("2027-02-28T21:00")


@HEADROOM_XFAIL
def test_b7_month_name_with_explicit_time():
    # "march 3 at 2pm": March < June -> 2027; 14:00 -> 18:00Z
    assert (_due("march 3 at 2pm") or "").startswith("2027-03-03T18:00")


@HEADROOM_XFAIL
def test_b7_month_abbrev_sept_four_letter():
    assert (_due("by sept 30") or "").startswith("2026-09-30T21:00")


@HEADROOM_XFAIL
def test_b7_month_name_october_default_time():
    assert (_due("october 10") or "").startswith("2026-10-10T21:00")


@HEADROOM_XFAIL
def test_b7_month_name_day_then_named_time():
    # "nov 11 5pm": Nov after June -> this year; 17:00 -> 21:00Z (fixed-offset engine)
    assert (_due("by nov 11 5pm") or "").startswith("2026-11-11T21:00")


@HEADROOM_XFAIL
def test_b7_month_name_same_month_future_day():
    # "june 30": same month, day 30 > today 24 -> this year
    assert (_due("june 30") or "").startswith("2026-06-30T21:00")


# ---------------- 5 guards (must hold before AND after the fix) ----------------

def test_b7_guard_iso_date_not_hijacked_high_conf():
    out = dr.resolve("by 2026-07-01", BASE)
    assert out["confidence"] == "high"
    assert (out["due_utc"] or "").startswith("2026-07-01")


def test_b7_guard_lone_month_no_day_stays_unparseable():
    # a month name with NO day must not invent a day; stays unparseable (due_utc None)
    assert _due("sometime in july") is None


def test_b7_guard_modal_may_is_not_month_may():
    # "may" as a modal verb (no adjacent day number) must not be read as the month May;
    # the friday weekday rule must still win -> upcoming Friday 06-26
    assert (_due("you may reply by friday") or "").startswith("2026-06-26")


def test_b7_guard_in_3_days_stays_calendar():
    assert (_due("in 3 days") or "").startswith("2026-06-27T21:00")


def test_b7_guard_bare_noon_still_1200():
    assert (_due("by noon") or "").startswith("2026-06-24T16:00")
