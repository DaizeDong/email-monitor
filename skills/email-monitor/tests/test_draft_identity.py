"""Explicit configuration owns draft identity; initialization preserves custom work."""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "skills" / "email-monitor" / "scripts"))
import em_draft_lint
import em_lint_rules
import init_config

FIX = json.loads(Path(__file__).with_name("drafting.json").read_text(encoding="utf-8"))


def initialize(out, monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["init_config.py", "--out", str(out), *args])
    return init_config.main()


@pytest.mark.parametrize("config", [None])
def test_missing_signature_refuses_to_choose_identity(config):
    with pytest.raises(ValueError, match="draft.signature"):
        em_lint_rules.lint(FIX["clean_ask"], "business", config=config)


def test_explicit_signature_accepts_only_the_selected_identity():
    assert em_lint_rules.lint(FIX["clean_ask"], "business", config=FIX["config"]) == []
    violations = em_lint_rules.lint(FIX["clean_ask"], "business", config=FIX["alternate_config"])
    assert any("signature" in violation for violation in violations)
