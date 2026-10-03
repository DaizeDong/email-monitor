"""Uninitialised must mean inert, not broken, and never a repo-internal
fallback. This repo has already leaked once through a documented in-repo
fallback path, so the absence of config must return None, and the repo must
contain no taxonomy of its own for anything to fall back to."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import em_topic  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_primary_config_override(monkeypatch):
    monkeypatch.delenv("EMAIL_MONITOR_CONFIG", raising=False)


@pytest.mark.parametrize("selection", ["primary", "alias", "both", "missing-primary"])
def test_config_override_priority_is_authoritative(tmp_path, monkeypatch, selection):
    fixture = json.loads(Path(__file__).with_name("reliability.json").read_text(encoding="utf-8"))
    case, account = fixture["type_map"], fixture["account"]["slug"]
    primary, alias = tmp_path / "primary", tmp_path / "alias"
    for directory, label in [(primary, case["source"]), (alias, case["types"][1])]:
        rules = directory / "rules"
        rules.mkdir(parents=True)
        (rules / "taxonomy.md").write_text(case["taxonomy"], encoding="utf-8")
        (rules / "sender_map.json").write_text(json.dumps({"by_address": {case["buckets"]["by_address"]: label}}), encoding="utf-8")
        (rules / "labels.json").write_text(json.dumps({account: [label], "_type_labels": case["types"]}), encoding="utf-8")
    monkeypatch.delenv("EMAIL_MONITOR_CONFIG_DIR", raising=False)
    if selection in {"primary", "both", "missing-primary"}:
        selected = tmp_path / "missing" if selection == "missing-primary" else primary
        monkeypatch.setenv("EMAIL_MONITOR_CONFIG", str(selected))
    if selection in {"alias", "both", "missing-primary"}:
        monkeypatch.setenv("EMAIL_MONITOR_CONFIG_DIR", str(alias))
    config = em_topic.load_config(account)
    if selection == "missing-primary":
        assert config is None
    else:
        expected = case["types"][1] if selection == "alias" else case["source"]
        assert config["allowed_labels"] == [expected]
        assert config["sender_map"]["by_address"][case["buckets"]["by_address"]] == expected


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


def test_type_labels_defaults_and_warns_when_unreachable(tmp_path, monkeypatch):
    """I-1b: TYPE_LABELS is a public guess at part of the private standard's
    spelling. When labels.json does not override it, and none of the guessed
    labels occur in this account's own allowed set, the type/source split R8
    exists for is inert for it -- that must be logged, not left silent."""
    d = tmp_path / "cfg" / "rules"
    d.mkdir(parents=True)
    (d / "taxonomy.md").write_text("x", encoding="utf-8")
    (d / "sender_map.json").write_text("{}", encoding="utf-8")
    (d / "labels.json").write_text(json.dumps({"dz": ["Accounts/Bank", "receipt"]}),
                                   encoding="utf-8")
    monkeypatch.setenv("EMAIL_MONITOR_CONFIG_DIR", str(tmp_path / "cfg"))

    warnings = []
    cfg = em_topic.load_config("dz", log=warnings.append)
    assert cfg["type_labels"] == list(em_topic.TYPE_LABELS)
    assert len(warnings) == 1
    assert "dz" in warnings[0]


def test_type_labels_override_from_labels_json_is_used_silently(tmp_path, monkeypatch):
    """An explicit override lives with the standard (one standard, invariant 1)
    and is trusted as-is: no warning fires even though the account's allowed
    set spells the type labels in lowercase, because the operator already told
    the loader what to look for."""
    d = tmp_path / "cfg" / "rules"
    d.mkdir(parents=True)
    (d / "taxonomy.md").write_text("x", encoding="utf-8")
    (d / "sender_map.json").write_text("{}", encoding="utf-8")
    (d / "labels.json").write_text(json.dumps({
        "dz": ["Accounts/Bank", "receipt"],
        "_type_labels": ["receipt"],
    }), encoding="utf-8")
    monkeypatch.setenv("EMAIL_MONITOR_CONFIG_DIR", str(tmp_path / "cfg"))

    warnings = []
    cfg = em_topic.load_config("dz", log=warnings.append)
    assert cfg["type_labels"] == ["receipt"]
    assert warnings == []


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
