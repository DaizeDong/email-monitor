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


def test_mapped_sender_still_reaches_the_model_but_cannot_have_its_source_overridden():
    """A mapped sender's SOURCE label can never come from the model: the map's
    answer is deterministic and wins regardless of what the model proposes.
    But whether the message is also a Receipt or Promo is a property of the
    individual message, so the model is still asked, just about that smaller
    question. This replaces the old assertion that the model was never called
    at all, which was true only because the old code never asked type
    questions on a pre-gate hit: the regression this file exists to catch."""
    calls = []

    def spy(**kw):
        calls.append(kw)
        return {"labels": [{"label": "Accounts/Shopping", "evidence": "whatever"}]}

    msg = {"from": "Shop <noreply@shop.example.com>", "subject": "Order shipped"}
    got = em_topic.judge(msg, TAXONOMY, SENDER_MAP, ALLOWED, call=spy)
    assert got["state"] == "decided"
    assert [l["label"] for l in got["labels"]] == ["Accounts/Shopping"]
    assert got["labels"][0]["source"] == "map"
    assert len(calls) == 1, "a mapped sender must still be asked about type labels"
    assert "- Accounts/Shopping" not in calls[0]["prompt"], (
        "the source label must not be offered as a choice once the map has settled it")


def test_good_model_reply_is_decided():
    def call(**kw):
        return {"labels": [{"label": "Receipt", "evidence": "has been processed"}]}

    got = em_topic.judge(MSG, TAXONOMY, SENDER_MAP, ALLOWED, call=call)
    assert got["state"] == "decided"
    assert got["labels"][0]["label"] == "Receipt"
    assert got["labels"][0]["source"] == "model"


def test_transport_failure_is_failed_and_writes_nothing():
    def call(**kw):
        raise RuntimeError("all providers exhausted")

    got = em_topic.judge(MSG, TAXONOMY, SENDER_MAP, ALLOWED, call=call)
    assert got["state"] == "failed"
    assert got["labels"] == []


def test_none_reply_is_failed():
    got = em_topic.judge(MSG, TAXONOMY, SENDER_MAP, ALLOWED, call=lambda **kw: None)
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


def test_empty_label_list_is_unsure():
    got = em_topic.judge(MSG, TAXONOMY, SENDER_MAP, ALLOWED,
                         call=lambda **kw: {"labels": []})
    assert got["state"] == "unsure"
    assert got["labels"] == []


def test_partial_survival_is_decided_with_the_survivor():
    def call(**kw):
        return {"labels": [
            {"label": "Receipt", "evidence": "has been processed"},
            {"label": "Promo", "evidence": "limited time offer"},
        ]}

    got = em_topic.judge(MSG, TAXONOMY, SENDER_MAP, ALLOWED, call=call)
    assert got["state"] == "decided"
    assert [l["label"] for l in got["labels"]] == ["Receipt"]
    assert [d["label"] for d in got["dropped"]] == ["Promo"]


def test_mapped_sender_still_gets_type_labels_from_the_model():
    """The regression this ruling exists for: a mapped sender must still be able to
    receive Receipt, because whether money moved is a property of the message."""
    msg = {"from": "Billing <service@shop.example.com>",
           "subject": "Receipt for your payment of $12.34"}
    sm = {"version": 1, "by_address": {"service@shop.example.com": "Accounts/Bank"}}
    calls = []

    def call(**kw):
        calls.append(kw)
        return {"labels": [{"label": "Receipt", "evidence": "Receipt for your payment"}]}

    got = em_topic.judge(msg, "tax", sm, ["Accounts/Bank", "Receipt", "Promo"], call=call)
    assert sorted(l["label"] for l in got["labels"]) == ["Accounts/Bank", "Receipt"]
    assert len(calls) == 1, "a mapped sender must still be asked about type labels"


def test_model_is_asked_only_about_type_labels_when_source_is_settled():
    msg = {"from": "Billing <service@shop.example.com>", "subject": "Anything"}
    sm = {"version": 1, "by_address": {"service@shop.example.com": "Accounts/Bank"}}
    seen = {}

    def call(prompt):
        seen["prompt"] = prompt
        return {"labels": []}

    em_topic.judge(msg, "tax", sm, ["Accounts/Bank", "Scholar", "Receipt"], call=call)
    assert "Receipt" in seen["prompt"]
    assert "Scholar" not in seen["prompt"], "source labels must not be re-litigated"


def test_transport_failure_keeps_a_settled_source_label():
    msg = {"from": "Billing <service@shop.example.com>", "subject": "Anything"}
    sm = {"version": 1, "by_address": {"service@shop.example.com": "Accounts/Bank"}}

    def boom(**kw):
        raise RuntimeError("chain exhausted")

    got = em_topic.judge(msg, "tax", sm, ["Accounts/Bank", "Receipt"], call=boom)
    assert got["state"] == "decided"
    assert [l["label"] for l in got["labels"]] == ["Accounts/Bank"]


def test_pregate_label_outside_allowed_is_dropped_not_written():
    """I-1a: a merged sender map can spell a label differently across accounts
    (the live finding this locks down: 'Accounts/GitHub' written to an account
    whose allowed set spells it 'Accounts/Github'). The pre-gate must be run
    through the same allowed check the model's labels get -- a hit for a label
    this account never listed must be dropped with a reason, not written."""
    msg = {"from": "Billing <pay@bank.example.com>", "subject": "Your receipt for $42.00"}
    sm = {"version": 1, "by_address": {"pay@bank.example.com": "Accounts/Bank"}}

    # A real transport that finds nothing further, so the failure being isolated here is
    # the pre-gate filter, not the unrelated "no transport supplied" path.
    got = em_topic.judge(msg, "tax", sm, ["accounts/bank", "receipt"],
                         call=lambda **kw: {"labels": []})
    assert got["labels"] == [], "a spelling drift must never reach the mailbox"
    assert got["dropped"][0]["label"] == "Accounts/Bank"
    assert got["dropped"][0]["source"] == "map"
    assert got["dropped"][0]["drop_reason"]


def test_unmapped_sender_is_asked_about_everything():
    msg = {"from": "Stranger <who@unknown.example.com>", "subject": "Hello"}
    seen = {}

    def call(prompt):
        seen["prompt"] = prompt
        return {"labels": []}

    em_topic.judge(msg, "tax", {}, ["Accounts/Bank", "Receipt"], call=call)
    assert "Accounts/Bank" in seen["prompt"] and "Receipt" in seen["prompt"]
