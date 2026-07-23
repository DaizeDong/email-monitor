import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from em_dates import normalize_due_at  # noqa: E402

# fixed reference "now" so the past/future window is deterministic (synthetic dates only)
NOW = datetime.datetime(2026, 7, 20, 12, 0, 0, tzinfo=datetime.timezone.utc)


def _local_utc(y, mo, d, h, mi):
    """What em_dates should produce for a NAIVE local wall-clock -- computed the same way (system
    local tz), so these assertions hold on any machine regardless of its timezone."""
    local = datetime.datetime.now().astimezone().tzinfo
    return (datetime.datetime(y, mo, d, h, mi, 0, tzinfo=local)
            .astimezone(datetime.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.000000+00:00"))


def test_iso_with_offset():
    # 15:45 at -04:00 == 19:45 UTC
    assert normalize_due_at("2026-08-03T15:45:00-04:00", now=NOW) == "2026-08-03T19:45:00.000000+00:00"


def test_iso_with_z_and_space_separator():
    assert normalize_due_at("2026-08-03T19:45:00Z", now=NOW) == "2026-08-03T19:45:00.000000+00:00"
    assert normalize_due_at("2026-08-03 19:45Z", now=NOW) == "2026-08-03T19:45:00.000000+00:00"


def test_naive_interpreted_in_local_tz():
    assert normalize_due_at("2026-08-03T14:00", now=NOW) == _local_utc(2026, 8, 3, 14, 0)


def test_date_only_defaults_to_9am_local():
    assert normalize_due_at("2026-08-03", now=NOW) == _local_utc(2026, 8, 3, 9, 0)


def test_past_beyond_grace_dropped():
    assert normalize_due_at("2026-07-01T10:00:00Z", now=NOW) is None


def test_recent_past_within_grace_kept():
    # yesterday relative to NOW -> a same-day deadline the owner may still act on
    assert normalize_due_at("2026-07-19T10:00:00Z", now=NOW) is not None


def test_far_future_dropped():
    assert normalize_due_at("2030-01-01T10:00:00Z", now=NOW) is None


def test_junk_returns_none_never_raises():
    # without a base, relative phrases are unresolvable -> None (the ISO path handles nothing here)
    for j in [None, "", "   ", "null", "none", "N/A", "tomorrow", "next Tuesday",
              "2026-13-40T10:00:00Z", "2026-08-03T99:99", 20260803, {"x": 1}]:
        assert normalize_due_at(j, now=NOW) is None


# --- em_duenorm delegation (relative / English natural-language, resolved against the mail Date) ---
BASE = "Wed, 22 Jul 2026 12:00:00 -0400"   # a mail Date header


def test_english_phrase_delegates_to_em_duenorm():
    # "August 3 at 3:45pm" EDT -> 19:45 UTC, resolved by em_duenorm against the mail date
    assert normalize_due_at("August 3 at 3:45pm", now=NOW, base=BASE) == "2026-08-03T19:45:00.000000+00:00"


def test_relative_phrase_needs_base():
    assert normalize_due_at("by friday 5pm", now=NOW, base=BASE) is not None
    assert normalize_due_at("by friday 5pm", now=NOW) is None       # no base -> cannot resolve


def test_absolute_iso_time_preserved_even_with_base():
    # a precise ISO time must be kept by the ISO path, NOT snapped to 17:00 by the phrase resolver
    assert normalize_due_at("2026-08-03T14:00-04:00", now=NOW, base=BASE) == "2026-08-03T18:00:00.000000+00:00"


def test_empty_or_vague_phrase_no_fake_softdue():
    # em_duenorm invents a soft-due for an empty phrase; we must reject that, never surface a fake date
    assert normalize_due_at("", now=NOW, base=BASE) is None
    assert normalize_due_at("see you soon", now=NOW, base=BASE) is None
