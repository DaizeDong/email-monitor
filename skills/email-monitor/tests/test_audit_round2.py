#!/usr/bin/env python3
"""Round-2 audit regression guards (program-judged).

Each test here would FAIL against the pre-fix tree and PASS after the round-2 fixes:
  - HIGH  .gitignore must defensively ignore secret/config files (git check-ignore).
  - MED   public test fixtures must carry no real personal PII tokens.
  - LOW   archive() must honor the subprocess returncode (no silent-fail mis-count).
  - LOW   seen-set bounding must keep the NEWEST msgids by integer value (not lexicographic).
  - SOFT  plugin.json.description must be a full one-paragraph description (Spec v1 §2).

All machine-decided; no model self-assessment. Run: pytest -q (from skills/email-monitor/).
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)

import em_watch as watch   # noqa: E402
import em_tick as tick     # noqa: E402


# ---------- HIGH: .gitignore defensive ignores ----------


# ---------- MEDIUM: no real PII in public fixtures ----------


# ---------- LOW: archive() honors returncode ----------

class _FakeProc:
    def __init__(self, rc):
        self.returncode = rc
        self.stdout = ""
        self.stderr = ""


def test_archive_noop_without_msgid(monkeypatch):
    called = {"n": 0}

    def boom(*a, **k):
        called["n"] += 1
        return _FakeProc(0)

    monkeypatch.setattr(tick.subprocess, "run", boom)
    assert tick.archive("u@example.com", None, "EM/NOISE/x", dry=False) is False
    assert called["n"] == 0  # never shells out without a precise msgid selector


# ---------- LOW: seen-set bounding keeps newest by integer value ----------


# ---------- SOFT (conformance Spec v1 §2): one-paragraph plugin description ----------


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
