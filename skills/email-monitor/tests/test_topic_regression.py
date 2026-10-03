# skills/email-monitor/tests/test_topic_regression.py
"""Run the kernel against the generated synthetic regression set.

Ruling R2 replaces the brief's original design here. The brief drove every case
through `judge` with a stub that quotes the whole subject verbatim, then asserted
no label came out. That cannot happen: verbatim evidence passes the evidence gate
and the label is in the allowed set, so `judge` correctly returns it. Rejecting
the tempting-but-wrong label is the TAXONOMY's job, and a stub never sees the
taxonomy at all -- so that assertion tested a behaviour the code under test has
no way to produce.

What is actually checkable offline is the evidence gate itself: a model that
paraphrases instead of quoting must never get a label through, on any of these
cases. What can only be checked against a real model is whether the taxonomy's
wording is strong enough to talk a model out of the tempting label when it DOES
quote verbatim -- that is the integration test below, skipped offline.

Note the positive control: a suite made only of "must not label" cases is passed
trivially by a kernel that never labels anything, which would be a useless
kernel that scores perfectly."""
import json
import os
import sys
import importlib.util
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import em_topic  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "topic_regression.jsonl")
_spec = importlib.util.spec_from_file_location(
    "topic_fixtures", Path(__file__).resolve().parents[3] / "tools/make_fixtures.py")
_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)
_policy = _fixtures.topic_regression_policy()
TAXONOMY, ALLOWED = _policy["taxonomy"], _policy["allowed_labels"]

integration = pytest.mark.integration


def rows():
    with open(FIXTURE, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def test_paraphrased_evidence_is_always_rejected():
    """Every case, driven with evidence the model 'summarised' rather than quoted.
    None of them may produce a label, regardless of how plausible the label is."""
    for r in rows():
        msg = {"from": r["from"], "subject": r["subject"]}
        stub = {"labels": [{"label": "Receipt",
                            "evidence": "this message is about a completed purchase"}]}
        got = em_topic.judge(msg, TAXONOMY, {}, ALLOWED, call=lambda **kw: stub)
        assert got["labels"] == [], "%s: paraphrased evidence must not yield a label" % r["shape"]
        assert got["state"] == "unsure"
        assert got["dropped"], "the drop must be recorded, not silent"


def test_verbatim_evidence_survives():
    """The positive control. Without it, a kernel that rejects everything scores
    perfectly against test_paraphrased_evidence_is_always_rejected alone."""
    r = [x for x in rows() if x["shape"] == "genuine-receipt-must-still-be-labelled"][0]
    msg = {"from": r["from"], "subject": r["subject"]}
    stub = {"labels": [{"label": "Receipt", "evidence": "Receipt for your payment"}]}
    got = em_topic.judge(msg, TAXONOMY, {}, ALLOWED, call=lambda **kw: stub)
    assert [l["label"] for l in got["labels"]] == ["Receipt"]
    assert got["state"] == "decided"


@integration
def test_taxonomy_shapes_against_a_real_model():
    """Use installed llmcall with synthetic messages; no mailbox/config is loaded.

    The offline suite supplies an empty chain. Run this file directly to exercise
    the installed route and the same assertions against a real model.
    """
    from llmcall import active_chain
    if not active_chain():
        pytest.skip("live model route is not enabled in the offline suite")
    import em_tick
    call = em_tick._make_transport()
    cases = rows()
    assert cases and any(case["expect_labels"] for case in cases)
    failures = []
    for case in cases:
        result = em_topic.judge(
            {"from": case["from"], "subject": case["subject"]},
            TAXONOMY, {}, ALLOWED, call=call)
        matched = result["state"] != "failed" and sorted(
            label["label"] for label in result["labels"]) == sorted(case["expect_labels"])
        print(json.dumps({"case": case["shape"], "matched": matched, "verdict": result}), flush=True)
        if not matched:
            failures.append(case["shape"])
    assert not failures, failures


if __name__ == "__main__":
    test_taxonomy_shapes_against_a_real_model()
    print("Live taxonomy regression passed all generated cases, including the positive control.")
