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


def _has_time(p):
    """True iff phrase carries any resolvable time-of-day token (numeric or named)."""
    if TIME_AMPM.search(p) or TIME_COLON.search(p):
        return True
    return ("noon" in p) or ("midnight" in p) or any(t in p for t in _COB)


def _apply_time(dt, phrase, default_hour=17):
    pl = phrase.lower()
    # Named time tokens take precedence over numeric scan.
    if "noon" in pl:
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
        return {"due_utc": d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "confidence": "inferred", "basis": "soft-due-next-business"}

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
    # end of (work) week -> upcoming friday
    if target is None and ("eow" in p or "end of week" in p):
        delta = (4 - base_dt.weekday()) % 7
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
                return {"due_utc": target.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "confidence": "med", "basis": "relative-hours"}
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
    return {"due_utc": target.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "confidence": conf, "basis": "phrase"}
