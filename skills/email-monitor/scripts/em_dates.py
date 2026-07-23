#!/usr/bin/env python3
"""email-monitor date extraction -- turn an appointment/deadline the classifier found in an email
into a pool-ready due_at, or nothing.

This is the email-monitor half of the optional "dated-reminder co-op": the agent classifier is asked
to emit a `due_at` string when an email states a concrete owner-facing date; this module VALIDATES and
NORMALIZES it into the base pool's UTC ISO format. Whether the value is then written as a dated reminder
is the caller's call, gated on schedule-reminder being installed -- so this logic runs (and is
unit-tested) with or without the base skill present.

Division of labour (no duplication): an ABSOLUTE ISO / date is normalized HERE, time-preserving, in
pure stdlib. A RELATIVE or English natural-language phrase ("by Friday", "August 3 at 3:45pm") is
delegated to the pre-existing deterministic resolver `em_duenorm`, which resolves it against the mail's
own Date header -- passed in as `base`. em_duenorm is imported lazily and optionally: absent it (or its
zoneinfo dep), the phrase path degrades to None and the ISO path still works.

normalize_due_at(raw):
  input   : whatever the model put in `due_at` (an ISO8601 string, a bare date, or junk/None).
  does    : parse it; a naive datetime is read in the SYSTEM LOCAL timezone (the owner's own machine,
            so no timezone is hardcoded); convert to UTC; drop anything unparseable, clearly in the
            past (a hallucinated/echoed old date), or absurdly far out.
  output  : "YYYY-MM-DDTHH:MM:SS.000000+00:00" (exactly the shape the pool stores), or None.
  on junk : None -- never raises, so a bad model date can never break a tick.
"""
import datetime
import re

# Full timestamp: 2026-08-03T15:45[:00][ offset|Z]  (T or space separator; optional seconds/offset)
_ISO_RE = re.compile(
    r"^\s*(\d{4})-(\d{2})-(\d{2})[T ](\d{1,2}):(\d{2})(?::(\d{2}))?\s*"
    r"(Z|[+-]\d{2}:?\d{2})?\s*$")
# Date only: 2026-08-03  -> defaults to DEFAULT_HOUR local time
_DATE_RE = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})\s*$")

DEFAULT_HOUR = 9          # a date with no time is assumed to be 09:00 the owner's local morning
PAST_GRACE_DAYS = 2       # allow "today/yesterday" (tz slop) but drop anything genuinely past
FUTURE_CAP_DAYS = 730     # 2 years: beyond this it is almost certainly a parse error / junk


def _local_tzinfo():
    """The system local timezone as a fixed-offset tzinfo -- the machine running email-monitor IS the
    owner's, so its local offset is the right lens for a naive '7/21 2pm'. Pure stdlib (no zoneinfo)."""
    return datetime.datetime.now().astimezone().tzinfo


def _parse_offset(tz):
    if tz == "Z":
        return datetime.timezone.utc
    tz = tz.replace(":", "")
    sign = 1 if tz[0] == "+" else -1
    return datetime.timezone(sign * datetime.timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5])))


def _resolve_phrase(phrase, base, now):
    """A non-ISO string (a relative or English natural-language deadline) -> hand it to the existing
    deterministic resolver em_duenorm, which resolves 'by Friday' / 'tomorrow' / 'August 3 at 3:45pm'
    against the mail's OWN Date header (base). This REUSES em_duenorm rather than duplicating it: ISO
    stays in this module because em_duenorm snaps an explicit clock time to 17:00 (wrong for a precise
    LLM ISO), and this module has no relative-phrase engine. Needs `base` (the mail Date) to resolve
    'relative to when'. em_duenorm is optional -- if it (or its zoneinfo dep) is absent, degrade to None."""
    if not base:
        return None
    try:
        import em_duenorm
        res = em_duenorm.normalize(phrase, base)
    except Exception:
        return None
    due_utc = res.get("due_utc")
    if not due_utc or res.get("basis") == "soft-due-next-business":
        return None                              # unparseable, or the empty-phrase fake soft-due
    return normalize_due_at(due_utc, now=now)    # re-validate the Z-form back through the ISO path


def normalize_due_at(raw, now=None, base=None):
    """See module docstring. `base` (the mail's Date header) enables the em_duenorm phrase fallback for
    non-ISO deadlines. Returns a pool-format UTC ISO string, or None."""
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if s.lower() in ("", "null", "none", "n/a", "na", "-"):
        return None

    dt = None
    m = _ISO_RE.match(s)
    if m:
        y, mo, d, h, mi, se, tz = m.groups()
        try:
            dt = datetime.datetime(int(y), int(mo), int(d), int(h), int(mi), int(se or 0))
        except ValueError:
            return None
        if tz:
            dt = dt.replace(tzinfo=_parse_offset(tz))
    else:
        m = _DATE_RE.match(s)
        if m:
            y, mo, d = m.groups()
            try:
                dt = datetime.datetime(int(y), int(mo), int(d), DEFAULT_HOUR, 0, 0)
            except ValueError:
                return None
    if dt is None:
        # Not an absolute ISO/date -> try the relative/natural-language path (em_duenorm) if we know
        # the mail's Date to resolve against. Absolute ISO is handled above (time-preserving).
        return _resolve_phrase(s, base, now)

    if dt.tzinfo is None:                      # naive -> interpret in the owner's local tz
        dt = dt.replace(tzinfo=_local_tzinfo())
    dt_utc = dt.astimezone(datetime.timezone.utc)

    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    if dt_utc < now - datetime.timedelta(days=PAST_GRACE_DAYS):
        return None                            # a past date the owner cannot act on -> drop
    if dt_utc > now + datetime.timedelta(days=FUTURE_CAP_DAYS):
        return None                            # absurdly far out -> almost certainly a parse error

    return dt_utc.strftime("%Y-%m-%dT%H:%M:%S.000000+00:00")
