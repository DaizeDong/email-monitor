#!/usr/bin/env python3
"""email-monitor deadline normalizer -> UTC RFC3339 (self-evolve signal #2: tz parse 0 errors).

Relative phrases ("by Friday", "tomorrow", "EOD") are resolved against the mail's Date header in
America/New_York, then converted to UTC. Confidence: explicit date = high, relative = med,
SLA-inferred = inferred. No deadline but actionable -> soft due (next business 17:00) + low conf.

Thin CLI shim: this module owns the argparse / zoneinfo / email.utils shell + base-date parsing;
the deterministic phrase->target resolution engine lives in em_duenorm_rules.py (pure re+datetime,
self-evolvable; mirrors em_lint_rules.py for the linter). normalize() composes parse_base + resolve.

Usage:
  echo '{"phrase":"by friday 5pm","base":"Wed, 25 Jun 2026 09:00:00 -0400"}' | python em_duenorm.py
"""
import argparse
import json
import sys
from datetime import timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    NY = timezone(timedelta(hours=-4))

from email.utils import parsedate_to_datetime
from datetime import datetime  # noqa: F401  (kept for backward-compat re-export surface)

# Pure resolution engine (re+datetime only) -- re-exported for backward compatibility.
from em_duenorm_rules import (  # noqa: F401
    resolve,
    _apply_time,
    WEEKDAYS,
    TIME_AMPM,
    TIME_COLON,
)


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


def normalize(phrase, base):
    """Return {due_utc, confidence, basis} or {due_utc:None} if unparseable."""
    return resolve(phrase, parse_base(base))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.parse_args()
    inp = json.loads(sys.stdin.read())
    print(json.dumps(normalize(inp.get("phrase"), inp.get("base")), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
