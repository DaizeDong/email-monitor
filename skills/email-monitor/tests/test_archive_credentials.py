#!/usr/bin/env python3
"""Regression guards for the archive credential path (program-judged).

Live bug these lock down (found 2026-07-12, NOISE archiving broken since the agent-first release):
`gmail-imap-label.py` authenticates from the GMAIL_APP_PW env var and exits 2 without it. The tick
set os.environ["GMAIL_APP_PW"] only around the IMAP fetch and popped it in a `finally` BEFORE the
record loop -- and archiving happens inside that loop. So every archive child ran with no password
and died with `rc=2 / ERROR no GMAIL_APP_PW`, silently, on every tick.

The fix injects the secret into the archive child's env *only*. It must NOT go back into
os.environ, because the same loop spawns the classifier CLIs (codex/cc/claude) -- leaking the Gmail
app password into an LLM subprocess's environment would be a real secret-egress bug.

Run: pytest -q (from skills/email-monitor/).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)

import em_tick as tick  # noqa: E402

PW = "test-app-pw-not-real"


class _Result:
    def __init__(self, rc=0):
        self.returncode = rc
        self.stdout = ""
        self.stderr = ""


def _capture(monkeypatch, rc=0):
    """Patch subprocess.run inside em_tick; return a dict that captures the call."""
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["env"] = kwargs.get("env")
        return _Result(rc)

    monkeypatch.setattr(tick.subprocess, "run", fake_run)
    monkeypatch.delenv("GMAIL_APP_PW", raising=False)
    return seen


def test_archive_injects_app_pw_into_child_env(monkeypatch):
    """The label tool exits 2 without GMAIL_APP_PW -- it must be in the child's env."""
    seen = _capture(monkeypatch)
    assert tick.archive("u@example.com", "1234567890", "EM/NOISE/x", dry=False, app_pw=PW) is True
    assert seen["env"] is not None, "archive() must pass an explicit env to the child"
    assert seen["env"].get("GMAIL_APP_PW") == PW


def test_archive_does_not_leak_app_pw_into_parent_env(monkeypatch):
    """The secret must never land in os.environ: the tick also spawns codex/cc/claude."""
    seen = _capture(monkeypatch)
    tick.archive("u@example.com", "1234567890", "EM/NOISE/x", dry=False, app_pw=PW)
    assert "GMAIL_APP_PW" not in os.environ
    # and the child's env is a copy, not os.environ itself
    assert seen["env"] is not os.environ


def test_archive_child_env_still_inherits_parent(monkeypatch):
    """Injecting the secret must not wipe the rest of the environment (PATH etc)."""
    seen = _capture(monkeypatch)
    monkeypatch.setenv("EM_CANARY", "keep-me")
    tick.archive("u@example.com", "1234567890", "EM/NOISE/x", dry=False, app_pw=PW)
    assert seen["env"].get("EM_CANARY") == "keep-me"


def test_archive_without_pw_does_not_fabricate_one(monkeypatch):
    """app_pw=None (the pre-fix call shape) must not silently inject an empty credential."""
    seen = _capture(monkeypatch)
    tick.archive("u@example.com", "1234567890", "EM/NOISE/x", dry=False)
    assert "GMAIL_APP_PW" not in (seen["env"] or {})


def test_archive_failure_is_still_reported(monkeypatch):
    """A non-zero child (e.g. rc=2 no-password) must still surface as False, never a silent True."""
    _capture(monkeypatch, rc=2)
    assert tick.archive("u@example.com", "1234567890", "EM/NOISE/x", dry=False, app_pw=PW) is False
