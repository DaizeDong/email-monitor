"""Explicit configuration owns draft identity; initialization preserves custom work."""
import copy
import io
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


@pytest.mark.parametrize("config", [None, {}, {"language": "en"}, {"draft": {}}])
def test_missing_signature_refuses_to_choose_identity(config):
    with pytest.raises(ValueError, match="draft.signature"):
        em_lint_rules.lint(FIX["clean_ask"], "business", config=config)


def test_explicit_signature_accepts_only_the_selected_identity():
    assert em_lint_rules.lint(FIX["clean_ask"], "business", config=FIX["config"]) == []
    violations = em_lint_rules.lint(FIX["clean_ask"], "business", config=FIX["alternate_config"])
    assert any("signature" in violation for violation in violations)


@pytest.mark.parametrize("config_kind", ["absent", "missing_signature", "malformed", "valid"])
def test_cli_reports_configuration_failure_without_changing_json_api(tmp_path, monkeypatch, capsys, config_kind):
    argv = ["em_draft_lint.py", "--json"]
    if config_kind != "absent":
        registry = tmp_path / "registry.json"
        data = {"draft": FIX["config"]} if config_kind == "valid" else {"draft": {}}
        registry.write_text("{" if config_kind == "malformed" else json.dumps(data), encoding="utf-8")
        argv += ["--config", str(registry)]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(FIX["clean_ask"].encode())))
    code = em_draft_lint.main()
    report = json.loads(capsys.readouterr().out)
    assert set(report) == {"clean", "profile", "violations"}
    assert code == (0 if config_kind == "valid" else 1)
    assert report["clean"] is (config_kind == "valid")
    if config_kind != "valid":
        assert any("invalid configuration" in violation for violation in report["violations"])


@pytest.mark.parametrize("config_key", [None, "alternate_config", "literal_config"])
def test_new_templates_use_the_selected_registry_and_pass_its_linter(private_companion, monkeypatch, config_key):
    out = private_companion
    if config_key:
        registry = copy.deepcopy(init_config.REGISTRY)
        registry["draft"] = FIX[config_key]
        registry_path = out / "registry.json"
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        original = registry_path.read_bytes()
    assert initialize(out, monkeypatch) == 0
    selected = json.loads((out / "registry.json").read_text(encoding="utf-8"))
    if config_key:
        assert (out / "registry.json").read_bytes() == original
    for path in sorted((out / "templates").glob("*.txt")):
        rendered = path.read_text(encoding="utf-8").format(name=FIX["recipient"], body=FIX["body"])
        assert rendered.splitlines()[-1] == selected["draft"]["signature"]
        assert em_lint_rules.lint(rendered, path.stem, config=selected) == []
    assert len(list((out / "templates").glob("*.txt"))) == 4


def test_existing_custom_template_and_registry_are_not_replaced(private_companion, monkeypatch):
    out = private_companion
    (out / "templates").mkdir(parents=True)
    registry = {"draft": FIX["alternate_config"]}
    (out / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
    template = out / "templates" / "business.txt"
    custom = FIX["custom_template"].format(signature=FIX["alternate_config"]["signature"]).encode()
    template.write_bytes(custom)
    assert initialize(out, monkeypatch) == 0
    assert template.read_bytes() == custom
    assert json.loads((out / "registry.json").read_text(encoding="utf-8")) == registry


@pytest.mark.parametrize("registry", ["{", "[]", "{}", '{"draft": {}}',
                                      '{"draft": {"signature": ""}}'])
def test_invalid_existing_registry_refuses_before_writing_templates(tmp_path, monkeypatch, capsys, registry):
    out = tmp_path / "config"
    out.mkdir()
    (out / "registry.json").write_text(registry, encoding="utf-8")
    before = {path.relative_to(out): path.read_bytes() for path in out.rglob("*") if path.is_file()}
    assert initialize(out, monkeypatch) == 1
    assert "draft" in capsys.readouterr().out.lower()
    assert {path.relative_to(out): path.read_bytes() for path in out.rglob("*") if path.is_file()} == before
