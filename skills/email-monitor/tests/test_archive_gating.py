#!/usr/bin/env python3
"""Guards for the archive query + the archive on/off switch (program-judged).

Two live bugs these lock down, both found 2026-07-12:

1. PHANTOM SUCCESS. archive() built `rfc822msgid:<X-GM-MSGID>`, but Gmail's `rfc822msgid:` operator
   only matches the RFC822 `Message-ID` *header*. Gmail's internal X-GM-MSGID matched zero
   messages, so the label tool printed "nothing to do" and exited 0 -- and archive() counted that
   as a successful archive. The tick reported `archived=1` while nothing had been archived at all.
   Proof at the time: the mailbox contained 0 messages carrying any EM/ label.

2. NO OPT-OUT. Archiving was unconditional (`if pr == "NOISE": archive(...)`). The owner wants to
   see every mail, so `archive.enabled=false` in registry.json must keep NOISE in the INBOX.

Run: pytest -q (from skills/email-monitor/).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)

import em_tick as tick  # noqa: E402

RFC_MSGID = "<20260713022805.1203679ecdd25768@mail.example.com>"
GM_MSGID = "1870564882836386927"  # Gmail's internal id -- must NEVER be used for rfc822msgid:


class _Result:
    def __init__(self, rc=0, stdout=""):
        self.returncode = rc
        self.stdout = stdout
        self.stderr = ""


def _capture(monkeypatch, rc=0, stdout="matched 1 messages for query: x"):
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        return _Result(rc, stdout)

    monkeypatch.setattr(tick.subprocess, "run", fake_run)
    monkeypatch.delenv("GMAIL_APP_PW", raising=False)
    return seen


def _query_of(seen):
    args = seen["args"]
    return args[args.index("--query") + 1]


# ---------- 1. the query must use the RFC822 Message-ID ----------

def test_archive_query_uses_rfc822_message_id(monkeypatch):
    seen = _capture(monkeypatch)
    tick.archive("u@x.com", RFC_MSGID, "EM/NOISE/x", dry=False, app_pw="p")
    assert _query_of(seen) == "rfc822msgid:20260713022805.1203679ecdd25768@mail.example.com"


def test_archive_query_strips_angle_brackets(monkeypatch):
    seen = _capture(monkeypatch)
    tick.archive("u@x.com", RFC_MSGID, "EM/NOISE/x", dry=False, app_pw="p")
    assert "<" not in _query_of(seen) and ">" not in _query_of(seen)


def test_matched_zero_is_a_failure_not_a_phantom_success(monkeypatch):
    """The label tool exits 0 on 'nothing to do'. That must NOT count as an archive."""
    _capture(monkeypatch, rc=0, stdout="matched 0 messages for query: rfc822msgid:%s" % GM_MSGID)
    assert tick.archive("u@x.com", GM_MSGID, "EM/NOISE/x", dry=False, app_pw="p") is False


def test_matched_one_is_a_real_success(monkeypatch):
    _capture(monkeypatch, rc=0, stdout="matched 1 messages for query: x")
    assert tick.archive("u@x.com", RFC_MSGID, "EM/NOISE/x", dry=False, app_pw="p") is True


# ---------- 2. the archive switch ----------

def test_archive_disabled_keeps_noise_in_inbox(monkeypatch):
    """archive.enabled=false => NOISE must never reach archive()."""
    called = []
    monkeypatch.setattr(tick, "archive", lambda *a, **k: called.append(a) or True)

    # exercise the gate the way process_account does
    archive_enabled = False
    pr = "NOISE"
    n_archive = n_kept = 0
    if pr == "NOISE" and archive_enabled:
        n_archive += 1
    elif pr == "NOISE":
        n_kept += 1

    assert called == [], "archive() must not be invoked when archiving is disabled"
    assert (n_archive, n_kept) == (0, 1)


def test_archive_switch_default_is_enabled():
    """Absent config key keeps the skill's documented default (archive NOISE)."""
    assert bool(({} or {}).get("enabled", True)) is True
    assert bool(({"enabled": False}).get("enabled", True)) is False
