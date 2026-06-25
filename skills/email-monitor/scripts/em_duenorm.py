#!/usr/bin/env python3
"""email-monitor deadline normalizer -> UTC RFC3339 (self-evolve signal #2: tz parse 0 errors).

Relative phrases ("by Friday", "tomorrow", "EOD") are resolved against the mail's Date header in
America/New_York, then converted to UTC. Confidence: explicit date = high, relative = med,
SLA-inferred = inferred. No deadline but actionable -> soft due (next business 17:00) + low conf.

Deterministic: same (phrase, base) -> same UTC. Stdlib only (zoneinfo).

Usage:
  echo '{"phrase":"by friday 5pm","base":"Wed, 25 Jun 2026 09:00:00 -0400"}' | python em_duenorm.py
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    NY = timezone(timedelta(hours=-4))

from email.utils import parsedate_to_datetime

WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6}
# A time token must carry am/pm OR a colon -- a bare integer (a date part, "in 3 days") is NOT a time.
TIME_AMPM = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.I)
TIME_COLON = re.compile(r"\b(\d{1,2}):(\d{2})\b")


def parse_base(base):
    """Parse the mail Date header into an aware datetime in NY tz."""
    if not base:
        dt = datetime.now(tz=NY)
    else:
        try:
            dt = parsedate_to_datetime(base)
        except Exception:
            dt = datetime.now(tz=NY)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=NY)
    return dt.astimezone(NY)


def _apply_time(dt, phrase, default_hour=17):
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


def normalize(phrase, base):
    """Return {due_utc, confidence, basis} or {due_utc:None} if unparseable."""
    base_dt = parse_base(base)
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
    if target is None:
        for name, idx in WEEKDAYS.items():
            if name in p:
                delta = (idx - base_dt.weekday()) % 7
                if delta == 0:
                    delta = 7  # "friday" means the upcoming friday, not today
                target = base_dt + timedelta(days=delta)
                break
    if target is None:
        m = re.search(r"in (\d+) (day|days|week|weeks|hour|hours)", p)
        if m:
            n = int(m.group(1))
            unit = m.group(2)
            if unit.startswith("day"):
                target = base_dt + timedelta(days=n)
            elif unit.startswith("week"):
                target = base_dt + timedelta(weeks=n)
            else:
                target = base_dt + timedelta(hours=n)
                return {"due_utc": target.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "confidence": "med", "basis": "relative-hours"}
    if target is None:
        return {"due_utc": None, "confidence": "low", "basis": "unparseable"}

    if "eod" in p or "end of day" in p:
        target = _apply_time(target, "5pm")
    else:
        target = _apply_time(target, p)
    return {"due_utc": target.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "confidence": conf, "basis": "phrase"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.parse_args()
    inp = json.loads(sys.stdin.read())
    print(json.dumps(normalize(inp.get("phrase"), inp.get("base")), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
