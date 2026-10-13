"""Observe installed llmcall policy at real callers using generated inputs."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import em_agent_classify as classifier
import em_quality_review as review
import em_tick as tick

FIX = json.loads((Path(__file__).parent / "reliability.json").read_text(encoding="utf-8"))
POLICY = FIX["model_policy"]


@pytest.fixture
def transport(monkeypatch):
    from test_standalone_cli_boundary import RESPONSES
    calls = []

    def call(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return SimpleNamespace(provider="synthetic-route", data={
            **POLICY["verdict"], "labels": [], "findings": RESPONSES['clean']['findings']})

    monkeypatch.setattr(classifier, "_llmcall", call)
    monkeypatch.setattr(tick.llmcall, "call", call)
    monkeypatch.setattr(tick, "log", lambda message: None)
    return calls


def assert_inherited(calls):
    assert len(calls) == 1
    prompt, arguments = calls[0]
    assert FIX["message"]["subject"] in prompt
    assert arguments["mode"] == "judge"
    assert not ({"chain", "providers", "model", "timeout", "fallback", "reasoning_effort"} & arguments.keys())


def test_heartbeat_classification_inherits_installed_policy(transport):
    assert tick.classify_record(FIX["message"], {}, {})["priority"] == "ACTION"
    assert_inherited(transport)


@pytest.mark.parametrize("settings", POLICY["legacy_registry"][:1])
def test_registry_overrides_fail_before_operational_work(tmp_path, monkeypatch, capsys, settings):
    config = tmp_path / "registry.json"
    config.write_text(json.dumps({"accounts": [FIX["account"]], "draft": FIX["draft"], **settings}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["em_tick.py", "--config", str(config)])
    monkeypatch.setattr(tick.em_runtime, "storage_config", lambda *a, **k: pytest.fail("operational setup reached"))
    assert tick.main() == 1
    report = json.loads(capsys.readouterr().out)
    assert "llmcall" in report["error"]
