#!/usr/bin/env python3
"""Pure deadline-resolution engine for email-monitor (self-evolve signal #2).

Extracted out of em_duenorm.py so the phrase->target resolution logic depends ONLY on
``re`` + ``datetime`` (no zoneinfo / email.utils / argparse). This keeps the engine inside the
self-evolve patch import-gate allowlist, i.e. it is self-evolvable (mirrors em_lint_rules.py for
the draft linter). The argparse/zoneinfo/email.utils shell stays in em_duenorm.py.

``resolve(phrase, base_dt)`` takes an ALREADY-PARSED aware datetime (the mail Date header in
America/New_York) and returns {due_utc, confidence, basis}. Deterministic: same input -> same UTC.
"""
import re
from datetime import datetime, timedelta, timezone

WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6}
# A time token must carry am/pm OR a colon -- a bare integer (a date part, "in 3 days") is NOT a time.
TIME_AMPM = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.I)
TIME_COLON = re.compile(r"\b(\d{1,2}):(\d{2})\b")
# Named "close of business" family -> 17:00 (matches the eod default).
_COB = ("cob", "close of business", "eod", "end of day")
# Named day-parts -> conventional hours. "noon" is handled separately via a WORD BOUNDARY so the
# substring inside "afternoon" does not collide.
DAYPARTS = (("morning", 9), ("afternoon", 14), ("evening", 18), ("night", 20))
_NOON = re.compile(r"\bnoon\b")

# Spelled-out month names (full + 3/4-letter abbrev). "may" doubles as a modal verb, so the rule
# only fires when a day-of-month number is adjacent (see _month_name_target), never on a lone word.
MONTHS = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
          "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
          "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
          "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12}
_MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))  # longest-first so "sept" beats "sep"
_MONTH_DAY = re.compile(r"\b(" + _MONTH_ALT + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:[,\s]+(\d{4}))?")
_DAY_MONTH = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + _MONTH_ALT + r")\b(?:[,\s]+(\d{4}))?")


def _month_name_target(p, base_dt):
    """Resolve a spelled-out month+day ("July 4" / "4 July") to a target datetime + confidence.

    Optional explicit year -> high confidence; otherwise the next occurrence (roll to next year
    if the month/day already passed this year) -> med. Returns (datetime, conf) or None.
    """
    m = _MONTH_DAY.search(p)
    if m:
        mon, day, yr = MONTHS[m.group(1)], int(m.group(2)), m.group(3)
    else:
        m = _DAY_MONTH.search(p)
        if not m:
            return None
        day, mon, yr = int(m.group(1)), MONTHS[m.group(2)], m.group(3)
    if day < 1:
        return None
    if yr:
        year, conf = int(yr), "high"
    else:
        year, conf = base_dt.year, "med"
        if (mon, day) < (base_dt.month, base_dt.day):
            year += 1
    probe = base_dt.replace(year=year, month=mon, day=1)
    day = min(day, _last_day_of_month(probe).day)  # clamp (e.g. feb 30 -> month end)
    return base_dt.replace(year=year, month=mon, day=day), conf


def _last_day_of_month(dt):
    if dt.month == 12:
        first_next = dt.replace(year=dt.year + 1, month=1, day=1)
    else:
        first_next = dt.replace(month=dt.month + 1, day=1)
    return first_next - timedelta(days=1)


def _has_time(p):
    """True iff phrase carries any resolvable time-of-day token (numeric or named)."""
    if TIME_AMPM.search(p) or TIME_COLON.search(p):
        return True
    if _NOON.search(p) or ("midnight" in p) or any(t in p for t in _COB):
        return True
    return any(re.search(r"\b" + name + r"\b", p) for name, _ in DAYPARTS)


def _apply_time(dt, phrase, default_hour=17):
    pl = phrase.lower()
    # Named day-parts take precedence (and must be checked before the bare "noon" token so that
    # "afternoon" maps to 14:00 rather than colliding with noon).
    for name, hour in DAYPARTS:
        if re.search(r"\b" + name + r"\b", pl):
            return dt.replace(hour=hour, minute=0, second=0, microsecond=0)
    if _NOON.search(pl):
        return dt.replace(hour=12, minute=0, second=0, microsecond=0)
    if "midnight" in pl:
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if any(t in pl for t in _COB):
        return dt.replace(hour=17, minute=0, second=0, microsecond=0)
    m = TIME_AMPM.search(phrase)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2) or 0)
        ap = m.group(3).lower()
        if ap == "pm" and hh < 12:
            hh += 12
        if ap == "am" and hh == 12:
            hh = 0
    else:
        m = TIME_COLON.search(phrase)
        if not m:
            return dt.replace(hour=default_hour, minute=0, second=0, microsecond=0)
        hh, mm = int(m.group(1)), int(m.group(2))
    hh = min(max(hh, 0), 23)
    return dt.replace(hour=hh, minute=mm, second=0, microsecond=0)


def _utc(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def resolve(phrase, base_dt):
    """Resolve a deadline phrase against an aware base datetime (NY tz).

    Returns {due_utc, confidence, basis} or {due_utc:None,...} if unparseable.
    """
    p = (phrase or "").strip().lower()
    if not p:
        # no deadline -> soft due next business day 17:00 NY
        d = base_dt + timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        d = d.replace(hour=17, minute=0, second=0, microsecond=0)
        return {"due_utc": _utc(d), "confidence": "inferred", "basis": "soft-due-next-business"}

    # asap / immediately / right away -> the mail's own moment, high urgency (do NOT snap to 17:00)
    if re.search(r"\b(asap|immediately|right away)\b", p):
        return {"due_utc": _utc(base_dt), "confidence": "high", "basis": "asap"}

    # within <N> hours -> relative offset from now (do NOT snap to a day boundary)
    mw = re.search(r"\bwithin\s+(the|a|an|\d+)\s+(hour|hours|day|days|week|weeks)\b", p)
    if mw:
        nraw = mw.group(1)
        n = 1 if nraw in ("the", "a", "an") else int(nraw)
        unit = mw.group(2)
        if unit.startswith("hour"):
            return {"due_utc": _utc(base_dt + timedelta(hours=n)),
                    "confidence": "med", "basis": "within-hours"}
        # within N days/weeks -> snap to 17:00 on that day below
        wt = base_dt + (timedelta(weeks=n) if unit.startswith("week") else timedelta(days=n))
        return {"due_utc": _utc(_apply_time(wt, p)), "confidence": "med", "basis": "within"}

    conf = "med"
    target = None

    # explicit absolute date e.g. 2026-07-01 or 07/01/2026 or "July 1"
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", p)
    if m:
        y, mo, da = map(int, m.groups())
        target = base_dt.replace(year=y, month=mo, day=da)
        conf = "high"
    if target is None:
        m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", p)
        if m:
            mo, da = int(m.group(1)), int(m.group(2))
            y = int(m.group(3)) if m.group(3) else base_dt.year
            if y < 100:
                y += 2000
            target = base_dt.replace(year=y, month=mo, day=da)
            conf = "high"
    if target is None and "today" in p:
        target = base_dt
    if target is None and "tomorrow" in p:
        target = base_dt + timedelta(days=1)
    # spelled-out month + day ("July 4" / "4 July"), optional year, next-occurrence rollover
    if target is None:
        mt = _month_name_target(p, base_dt)
        if mt is not None:
            target, conf = mt
    # end of month / eom -> last calendar day of the current month
    if target is None and ("end of month" in p or "end of the month" in p or re.search(r"\beom\b", p)):
        target = _last_day_of_month(base_dt)
    # end of (work) week -> upcoming friday
    if target is None and ("eow" in p or "end of week" in p):
        delta = (4 - base_dt.weekday()) % 7
        target = base_dt + timedelta(days=delta)
    # this weekend -> upcoming Saturday
    if target is None and "weekend" in p:
        delta = (5 - base_dt.weekday()) % 7
        if delta == 0:
            delta = 7
        target = base_dt + timedelta(days=delta)
    # next week (no specific weekday) -> Monday of next week
    if target is None and re.search(r"\bnext week\b", p):
        delta = (0 - base_dt.weekday()) % 7
        if delta == 0:
            delta = 7
        target = base_dt + timedelta(days=delta)
    if target is None:
        for name, idx in WEEKDAYS.items():
            if name in p:
                delta = (idx - base_dt.weekday()) % 7
                if delta == 0:
                    delta = 7  # "friday" means the upcoming friday, not today
                if re.search(r"\bnext\s+" + name, p):
                    delta += 7  # "next friday" = the friday a week after the upcoming one
                target = base_dt + timedelta(days=delta)
                break
    # N business days (skip weekends), with or without an "in" prefix
    if target is None:
        mb = re.search(r"\b(a|an|\d+)\s+business\s+days?\b", p)
        if mb:
            nraw = mb.group(1)
            n = 1 if nraw in ("a", "an") else int(nraw)
            d = base_dt
            added = 0
            while added < n:
                d += timedelta(days=1)
                if d.weekday() < 5:
                    added += 1
            target = d
    if target is None:
        m = re.search(r"in (a|an|\d+) (day|days|week|weeks|hour|hours)", p)
        if m:
            nraw = m.group(1)
            n = 1 if nraw in ("a", "an") else int(nraw)
            unit = m.group(2)
            if unit.startswith("day"):
                target = base_dt + timedelta(days=n)
            elif unit.startswith("week"):
                target = base_dt + timedelta(weeks=n)
            else:
                target = base_dt + timedelta(hours=n)
                return {"due_utc": _utc(target), "confidence": "med", "basis": "relative-hours"}
    # ordinal day-of-month "the 15th" -> that day this month, or next month if already passed
    if target is None:
        mo = re.search(r"\bthe (\d{1,2})(?:st|nd|rd|th)\b", p)
        if mo:
            day = int(mo.group(1))
            if 1 <= day <= 28 or day <= _last_day_of_month(base_dt).day:
                if day >= base_dt.day:
                    target = base_dt.replace(day=day)
                elif base_dt.month == 12:
                    target = base_dt.replace(year=base_dt.year + 1, month=1, day=day)
                else:
                    nxt = base_dt.replace(month=base_dt.month + 1, day=1)
                    target = nxt.replace(day=min(day, _last_day_of_month(nxt).day))
    # bare time with no date -> anchor to the mail's own day
    if target is None and _has_time(p):
        target = base_dt
        conf = "med"
    if target is None:
        return {"due_utc": None, "confidence": "low", "basis": "unparseable"}

    if "eod" in p or "end of day" in p:
        target = _apply_time(target, "5pm")
    else:
        target = _apply_time(target, p)
    return {"due_utc": _utc(target), "confidence": conf, "basis": "phrase"}
