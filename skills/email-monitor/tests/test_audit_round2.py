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
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)

import em_watch as watch   # noqa: E402
import em_tick as tick     # noqa: E402


def _repo_root():
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=HERE,
                             capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return None


# ---------- HIGH: .gitignore defensive ignores ----------

SECRET_PATHS = [".env", "config.json", ".credentials.json", "secrets.json",
                "registry.json", "resolve-cred.ps1", "x.cred", "rules/merged.json"]


@pytest.mark.parametrize("relpath", SECRET_PATHS)
def test_secret_files_are_gitignored(relpath):
    root = _repo_root()
    if not root:
        pytest.skip("not a git checkout")
    p = subprocess.run(["git", "check-ignore", relpath], cwd=root,
                       capture_output=True, text=True)
    # exit 0 == path is ignored; exit 1 == NOT ignored (the audit-flagged hole)
    assert p.returncode == 0, "%s is NOT gitignored (secret could leak)" % relpath


# ---------- MEDIUM: no real PII in public fixtures ----------

# split fragments on purpose: the scanner must not match itself, and the literals must survive a
# history rewrite that replaces real identifiers with synthetic stand-ins.
PII_DENYLIST = ["exampleemp" + "loyer", "the" + "exampleresi" + "dence", "<account>" + "2019"]


def test_no_real_pii_in_public_fixtures():
    targets = [os.path.join(HERE, "golden_classify.jsonl"),
               os.path.join(HERE, "test_acceptance.py")]
    leaks = []
    for path in targets:
        if not os.path.isfile(path):
            continue
        text = open(path, encoding="utf-8").read().lower()
        for tok in PII_DENYLIST:
            if tok in text:
                leaks.append("%s in %s" % (tok, os.path.basename(path)))
    assert not leaks, "real PII present in public fixtures: %s" % leaks


# ---------- LOW: archive() honors returncode ----------

class _FakeProc:
    def __init__(self, rc):
        self.returncode = rc
        self.stdout = ""
        self.stderr = ""


def test_archive_returns_false_on_subprocess_failure(monkeypatch):
    monkeypatch.setattr(tick.subprocess, "run", lambda *a, **k: _FakeProc(1))
    assert tick.archive("u@example.com", "1234567890", "EM/NOISE/x", dry=False) is False


def test_archive_returns_true_on_subprocess_success(monkeypatch):
    monkeypatch.setattr(tick.subprocess, "run", lambda *a, **k: _FakeProc(0))
    assert tick.archive("u@example.com", "1234567890", "EM/NOISE/x", dry=False) is True


def test_archive_noop_without_msgid(monkeypatch):
    called = {"n": 0}

    def boom(*a, **k):
        called["n"] += 1
        return _FakeProc(0)

    monkeypatch.setattr(tick.subprocess, "run", boom)
    assert tick.archive("u@example.com", None, "EM/NOISE/x", dry=False) is False
    assert called["n"] == 0  # never shells out without a precise msgid selector


# ---------- LOW: seen-set bounding keeps newest by integer value ----------

def test_bound_seen_keeps_newest_not_lexicographic():
    # "1000"/"100" are the two newest by value; lexicographic sort[-2:] would wrongly
    # keep {"2","99"} and DROP "1000" -> re-report risk. Integer order must win.
    seen = {"2", "100", "99", "1000"}
    kept = set(watch.bound_seen(seen, cap=2))
    assert kept == {"1000", "100"}, kept
    assert "1000" in kept and "2" not in kept


def test_bound_seen_no_truncation_under_cap():
    seen = {"5", "10", "3"}
    assert set(watch.bound_seen(seen, cap=50000)) == seen


def test_bound_seen_nonnumeric_sorts_oldest():
    # malformed/non-numeric ids must not be kept over real (numeric) recent ones
    seen = {"weird", "1000", "2000"}
    kept = set(watch.bound_seen(seen, cap=2))
    assert kept == {"1000", "2000"}


# ---------- SOFT (conformance Spec v1 §2): one-paragraph plugin description ----------

def test_plugin_description_is_full_paragraph():
    root = _repo_root() or os.path.abspath(os.path.join(HERE, "..", "..", ".."))
    pj = os.path.join(root, ".claude-plugin", "plugin.json")
    data = json.load(open(pj, encoding="utf-8"))
    desc = data.get("description", "")
    assert len(desc) >= 200, "plugin.json description too short for Spec v1 (%d chars)" % len(desc)
    low = desc.lower()
    # capability surface + a trigger/scope cue should appear
    assert "classif" in low and "draft" in low
    assert ("when you" in low or "use when" in low)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
