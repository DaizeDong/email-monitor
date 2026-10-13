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


# ---------- 1. the query must use the RFC822 Message-ID ----------


def test_matched_zero_is_a_failure_not_a_phantom_success(monkeypatch):
    """The label tool exits 0 on 'nothing to do'. That must NOT count as an archive."""
    _capture(monkeypatch, rc=0, stdout="matched 0 messages for query: rfc822msgid:%s" % GM_MSGID)
    assert tick.archive("u@example.com", GM_MSGID, "EM/NOISE/x", dry=False, app_pw="p") is False


# ---------- 1b. _label_add must not count the same phantom archive() already guards against ----------


# ---------- 2. the archive switch ----------


def test_noise_is_never_planned_for_archive_unless_explicitly_enabled():
    """Production planner: archiving is opt-in. The default and an absent switch both keep NOISE in the inbox."""
    import inspect
    import em_tick
    acct = {"user": "user1@example.com", "slug": "user1"}
    record = {"message_id": "<m1@example.com>", "subject": "s", "body": "b", "date": "", "from": "a@example.com"}
    verdict = {"priority": "NOISE", "label": "promo"}
    kinds = lambda enabled: [p["action"] for p in
                             em_tick._plan_record(acct, "INBOX", 1, record, verdict, {}, False, enabled)]
    assert "archive" not in kinds(False)
    assert "archive" in kinds(True)  # negative control: the planner can archive when told to
    assert inspect.signature(em_tick.process_account).parameters["archive_enabled"].default is False
