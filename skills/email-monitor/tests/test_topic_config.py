"""Uninitialised must mean inert, not broken, and never a repo-internal
fallback. This repo has already leaked once through a documented in-repo
fallback path, so the absence of config must return None, and the repo must
contain no taxonomy of its own for anything to fall back to."""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import em_topic  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_primary_config_override(monkeypatch):
    monkeypatch.delenv("EMAIL_MONITOR_CONFIG", raising=False)


def test_missing_config_returns_none_not_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("EMAIL_MONITOR_CONFIG_DIR", str(tmp_path / "nonexistent"))
    assert em_topic.load_config("dz") is None


def test_repo_ships_no_taxonomy_to_fall_back_to():
    """The structural half of the same rule: there must be nothing in the repo
    that a future in-repo fallback could point at."""
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True, check=True).stdout.strip()
    tracked = subprocess.run(["git", "ls-files"], cwd=root,
                             capture_output=True, text=True, check=True).stdout.split()
    offenders = [p for p in tracked
                 if os.path.basename(p) in ("taxonomy.md", "sender_map.json", "labels.json")]
    assert offenders == [], "topic config must live only in the private companion repo"
