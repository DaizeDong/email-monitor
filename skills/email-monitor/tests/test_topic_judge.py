# skills/email-monitor/tests/test_topic_judge.py
"""judge() must abstain in every direction it can be wrong: a broken call, an
unparseable reply, a label outside the taxonomy, and a label whose evidence does
not check out. All four write nothing."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import em_topic  # noqa: E402

TAXONOMY = "Receipt means proof that money already moved."
ALLOWED = ["Receipt", "Promo", "Accounts/Shopping"]
SENDER_MAP = {"version": 1, "by_address": {"noreply@shop.example.com": "Accounts/Shopping"}}
MSG = {"from": "Billing <billing@example.com>",
       "subject": "Your payment of $42.00 has been processed"}


def test_transport_failure_is_failed_and_writes_nothing():
    def call(**kw):
        raise RuntimeError("all providers exhausted")

    got = em_topic.judge(MSG, TAXONOMY, SENDER_MAP, ALLOWED, call=call)
    assert got["state"] == "failed"
    assert got["labels"] == []


def test_label_outside_taxonomy_is_dropped():
    def call(**kw):
        return {"labels": [{"label": "NotARealLabel", "evidence": "payment"}]}

    got = em_topic.judge(MSG, TAXONOMY, SENDER_MAP, ALLOWED, call=call)
    assert got["state"] == "unsure"
    assert got["labels"] == []
    assert got["dropped"][0]["label"] == "NotARealLabel"


def test_unverifiable_evidence_yields_unsure_not_decided():
    def call(**kw):
        return {"labels": [{"label": "Receipt", "evidence": "a refund was issued"}]}

    got = em_topic.judge(MSG, TAXONOMY, SENDER_MAP, ALLOWED, call=call)
    assert got["state"] == "unsure"
    assert got["labels"] == []
    assert got["dropped"][0]["drop_reason"]
