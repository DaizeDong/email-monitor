"""Uninitialised must mean inert, not broken, and never a repo-internal
fallback. This repo has already leaked once through a documented in-repo
fallback path, so the absence of config must return None, and the repo must
contain no taxonomy of its own for anything to fall back to."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import em_topic  # noqa: E402


def test_missing_config_returns_none_not_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("EMAIL_MONITOR_CONFIG_DIR", str(tmp_path / "nonexistent"))
    assert em_topic.load_config("dz") is None


def test_partial_config_returns_none(tmp_path, monkeypatch):
    """A taxonomy with no allowed label set is not a usable config. Half a
    config must be treated as no config, not as a config with defaults."""
    d = tmp_path / "cfg" / "rules"
    d.mkdir(parents=True)
    (d / "taxonomy.md").write_text("Receipt means money moved.", encoding="utf-8")
    monkeypatch.setenv("EMAIL_MONITOR_CONFIG_DIR", str(tmp_path / "cfg"))
    assert em_topic.load_config("dz") is None


def test_complete_config_loads(tmp_path, monkeypatch):
    d = tmp_path / "cfg" / "rules"
    d.mkdir(parents=True)
    (d / "taxonomy.md").write_text("Receipt means money moved.", encoding="utf-8")
    (d / "sender_map.json").write_text(json.dumps(
        {"version": 1, "by_address": {"a@example.com": "Receipt"}}), encoding="utf-8")
    (d / "labels.json").write_text(json.dumps({"dz": ["Receipt", "Promo"]}),
                                   encoding="utf-8")
    monkeypatch.setenv("EMAIL_MONITOR_CONFIG_DIR", str(tmp_path / "cfg"))
    cfg = em_topic.load_config("dz")
    assert cfg["allowed_labels"] == ["Receipt", "Promo"]
    assert cfg["sender_map"]["by_address"]["a@example.com"] == "Receipt"
    assert "money moved" in cfg["taxonomy"]


def test_unknown_account_returns_none(tmp_path, monkeypatch):
    d = tmp_path / "cfg" / "rules"
    d.mkdir(parents=True)
    (d / "taxonomy.md").write_text("x", encoding="utf-8")
    (d / "sender_map.json").write_text("{}", encoding="utf-8")
    (d / "labels.json").write_text(json.dumps({"dz": ["Receipt"]}), encoding="utf-8")
    monkeypatch.setenv("EMAIL_MONITOR_CONFIG_DIR", str(tmp_path / "cfg"))
    assert em_topic.load_config("no_such_account") is None


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
