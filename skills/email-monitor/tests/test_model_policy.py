"""Observe installed llmcall policy at real callers using generated inputs."""
import copy
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
    calls = []

    def call(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return SimpleNamespace(provider="synthetic-route", data={
            **POLICY["verdict"], "labels": [], "findings": []})

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


def test_direct_classification_inherits_installed_policy(transport):
    assert classifier.classify(FIX["message"])["priority"] == "ACTION"
    assert_inherited(transport)
    extract = transport[0][1]["extract"]
    assert extract(json.dumps(POLICY["verdict"])) == POLICY["verdict"]
    assert extract('{"priority": "invalid"}') is None


def test_heartbeat_classification_inherits_installed_policy(transport):
    assert tick.classify_record(FIX["message"], {}, {})["priority"] == "ACTION"
    assert_inherited(transport)


@pytest.mark.parametrize("durable", [False, True])
def test_topic_callers_inherit_installed_policy(transport, monkeypatch, durable):
    monkeypatch.setattr(tick.em_topic, "load_config", lambda *a, **k: copy.deepcopy(POLICY["topic_config"]))
    record = {**FIX["message"], "mailbox": "INBOX", "uidvalidity": FIX["cursor"]["uidvalidity"]}
    if durable:
        state = {"topic_retry": [], "actions": {}, "cursors": {}}
        tick._plan_topics(state, FIX["account"], [record], None, True)
        assert state["topic_retry"] == [] and state["actions"] == {}
    else:
        assert tick.topic_label(FIX["account"]["user"], FIX["account"]["slug"], [record], True) == 0
    assert_inherited(transport)


def test_quality_review_inherits_installed_policy(transport):
    message = FIX["message"]
    assert review.judge([(message["from"], message["subject"], "Review")], POLICY["taxonomy"]) == []
    assert_inherited(transport)


def test_quality_review_distinguishes_transport_failure(monkeypatch):
    monkeypatch.setattr(review.llmcall, "call", lambda *a, **k: None)
    message = FIX["message"]
    assert review.judge([(message["from"], message["subject"], "Review")], POLICY["taxonomy"]) is None


@pytest.mark.parametrize("overrides", POLICY["legacy_api"])
def test_obsolete_classifier_api_controls_are_explicit_errors(transport, overrides):
    with pytest.raises(ValueError, match="llmcall"):
        classifier.classify(FIX["message"], **overrides)
    assert transport == []


@pytest.mark.parametrize("flags", POLICY["legacy_cli"])
def test_obsolete_classifier_cli_controls_fail_before_input(monkeypatch, capsys, flags):
    monkeypatch.setattr(sys, "argv", ["em_agent_classify.py", *flags])
    with pytest.raises(SystemExit) as stopped:
        classifier.main()
    assert stopped.value.code == 2
    assert "llmcall" in capsys.readouterr().err


@pytest.mark.parametrize("settings", POLICY["legacy_registry"])
def test_registry_overrides_fail_before_operational_work(tmp_path, monkeypatch, capsys, settings):
    config = tmp_path / "registry.json"
    config.write_text(json.dumps({"accounts": [FIX["account"]], "draft": FIX["draft"], **settings}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["em_tick.py", "--config", str(config)])
    monkeypatch.setattr(tick.em_runtime, "storage_config", lambda *a, **k: pytest.fail("operational setup reached"))
    assert tick.main() == 1
    report = json.loads(capsys.readouterr().out)
    assert "llmcall" in report["error"]


@pytest.mark.parametrize("entrypoint", ["transport", "topic", "review"])
def test_legacy_topic_and_review_timeouts_are_explicit_errors(transport, entrypoint):
    with pytest.raises(ValueError, match="llmcall"):
        if entrypoint == "transport":
            tick._make_transport(17)
        elif entrypoint == "topic":
            tick.topic_label(FIX["account"]["user"], FIX["account"]["slug"], [], True, timeout=17)
        else:
            review.judge([], POLICY["taxonomy"], timeout=17)
    assert transport == []
