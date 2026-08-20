"""The evidence gate must be able to reject. A test suite that only feeds it
valid evidence proves nothing: a gate that returns True unconditionally would
pass every such test."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import em_topic  # noqa: E402

MSG = {
    "from": "Billing <billing@example.com>",
    "subject": "Your payment of $42.00 has been processed",
}


def test_span_present_in_subject_holds():
    assert em_topic.evidence_holds("payment of $42.00 has been processed", MSG)


def test_span_present_in_from_holds():
    assert em_topic.evidence_holds("billing@example.com", MSG)


def test_span_absent_is_rejected():
    assert not em_topic.evidence_holds("refund issued", MSG)


def test_paraphrase_is_rejected():
    """The model paraphrasing instead of quoting is the common failure, and it
    must not pass. This is the negative control for the whole gate."""
    assert not em_topic.evidence_holds("a payment was processed", MSG)


def test_empty_evidence_is_rejected():
    assert not em_topic.evidence_holds("", MSG)
    assert not em_topic.evidence_holds(None, MSG)


def test_case_and_whitespace_are_normalised():
    assert em_topic.evidence_holds("YOUR   PAYMENT of $42.00", MSG)


def test_verify_labels_splits_kept_from_dropped():
    proposed = [
        {"label": "Receipt", "evidence": "has been processed", "source": "model"},
        {"label": "Travel", "evidence": "flight to Boston", "source": "model"},
    ]
    kept, dropped = em_topic.verify_labels(proposed, MSG)
    assert [k["label"] for k in kept] == ["Receipt"]
    assert [d["label"] for d in dropped] == ["Travel"]
    assert dropped[0]["drop_reason"]
