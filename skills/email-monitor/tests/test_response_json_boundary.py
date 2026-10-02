"""Generated response-boundary cases; source of truth is tools/make_fixtures.py."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "skills" / "email-monitor" / "scripts"))
import em_agent_classify as classifier

_spec = importlib.util.spec_from_file_location(
    "email_response_parser_fixtures", ROOT / "tools" / "make_fixtures.py")
_generator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_generator)
CASES = _generator.response_parser_cases()


def classify_reply(monkeypatch, text):
    requests = []
    def transport(prompt, **options):
        requests.append((prompt, options))
        assert options["mode"] == "judge"
        assert options["extract"] is classifier._valid_verdict
        data = options["extract"](text)
        return None if data is None else SimpleNamespace(data=data, provider="synthetic")
    monkeypatch.setattr(classifier, "_llmcall", transport)
    result = classifier.classify({
        "from": "user1@example.com", "subject": "Synthetic review request",
        "body": "Please review the AcmeCorp example."})
    assert len(requests) == 1
    return result


@pytest.mark.parametrize("case", CASES["malformed"], ids=lambda case: case["id"])
def test_malformed_outer_never_becomes_an_example_verdict(monkeypatch, case):
    assert classifier._extract_json(case["text"]) is None
    assert classifier._valid_verdict(case["text"]) is None
    assert classify_reply(monkeypatch, case["text"]) is None


@pytest.mark.parametrize("case", CASES["valid"], ids=lambda case: case["id"])
def test_valid_outer_reply_preserves_its_actual_priority(monkeypatch, case):
    assert classifier._extract_json(case["text"]) == case["expected"]
    assert classifier._valid_verdict(case["text"]) == case["expected"]
    result = classify_reply(monkeypatch, case["text"])
    assert result["priority"] == case["expected"]["priority"]
    assert result["label"] == case["expected"]["label"]
    assert result["reason"] == case["expected"]["reason"]


@pytest.mark.parametrize("case", CASES["nonverdict"], ids=lambda case: case["id"])
def test_nonobject_json_is_not_an_accepted_verdict(monkeypatch, case):
    assert classifier._valid_verdict(case["text"]) is None
    assert classify_reply(monkeypatch, case["text"]) is None


@pytest.mark.parametrize("case", CASES["limits"], ids=lambda case: case["id"])
def test_decoder_input_limits_remain_invalid_responses(monkeypatch, case):
    assert classifier._extract_json(case["text"]) is None
    assert classifier._valid_verdict(case["text"]) is None
    assert classify_reply(monkeypatch, case["text"]) is None


@pytest.mark.parametrize("case", CASES["controlled_errors"], ids=lambda case: case["id"])
def test_decoder_input_errors_stay_within_the_extract_contract(monkeypatch, case):
    real_json = classifier.json
    error = {"ValueError": ValueError, "RecursionError": RecursionError}[case["error"]]

    def rejected_decode(*args):
        raise error("synthetic decoder input limit")

    def loads(text):
        if case["site"] == "loads":
            return rejected_decode(text)
        return real_json.loads(text)

    facade = SimpleNamespace(
        loads=loads, JSONDecoder=lambda: SimpleNamespace(raw_decode=rejected_decode))
    monkeypatch.setattr(classifier, "json", facade)
    text = '{"priority":"ACTION"}'
    if case["site"] == "raw_decode":
        text = "Synthetic model answer: " + text
    assert classifier._extract_json(text) is None
    assert classifier._valid_verdict(text) is None
    assert classify_reply(monkeypatch, text) is None
