#!/usr/bin/env python3
"""Tests for the deferred retry of `failed` topic verdicts.

The behaviour under test is the one that used to lose mail silently: a message
whose verdict came back `failed` (the model chain was unreachable, so nothing was
ever judged) must survive the tick and be judged on a later one. The INBOX cursor
advances regardless, so if the message is not held here it is gone for good.

Each test states the failure it would catch. A test that cannot fail is worth
nothing, so the round-trip case asserts the label is applied on the SECOND pass
and not the first: an implementation that simply drops `failed` records passes
neither half.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import em_retry  # noqa: E402


def rec(mid, subject="Subject", frm="a@example.com"):
    return {"message_id": mid, "from": frm, "subject": subject,
            "date": "Mon, 1 Sep 2026 00:00:00 +0000", "list_id": ""}


# ---------- queue mechanics ----------


def test_enqueue_is_idempotent_on_message_id():
    # The same message can fail again on a retry. Two copies would double every
    # future attempt and let one message burn the whole budget.
    q = em_retry.enqueue([], rec("m1"), "first")
    q = em_retry.enqueue(q, rec("m1"), "second")
    assert len(q) == 1
    assert q[0]["last_error"] == "second"


# ---------- integration with topic_label ----------

@pytest.fixture()
def tick(monkeypatch):
    import em_tick
    import em_topic
    monkeypatch.setattr(em_tick, "log", lambda *a, **k: None)
    monkeypatch.setattr(em_topic, "load_config", lambda slug, log=None: {
        "taxonomy": "std", "sender_map": {"by_address": {}},
        "allowed_labels": ["Life/Health"], "type_labels": [],
    })
    return em_tick


def test_failed_verdict_is_retried_on_the_next_tick(tick, monkeypatch):
    """The regression this whole module exists for.

    Pass 1: the provider chain is down -> `failed` -> nothing labelled, message held.
    Pass 2: the chain is back -> the SAME message is judged and labelled.

    An implementation that drops `failed` records fails the second assertion, and one
    that labels on the first pass fails the first, so neither half can pass by accident.
    """
    applied = []
    monkeypatch.setattr(tick, "_label_add",
                        lambda u, mid, lab, dry, app_pw=None: applied.append((mid, lab)) or True)

    outage = {"on": True}

    def judge(msg, *a, **k):
        if outage["on"]:
            return {"state": "failed", "labels": [], "reason": "chain down"}
        return {"state": "decided",
                "labels": [{"label": "Life/Health", "evidence": msg["subject"]}]}

    import em_topic
    monkeypatch.setattr(em_topic, "judge", judge)

    state = {}
    records = [rec("m1", subject="Life/Health matters")]

    n = tick.topic_label("u", "slug", records, dry=False, state=state)
    assert n == 0, "nothing may be labelled while the model is unreachable"
    assert applied == []
    assert [e["message_id"] for e in em_retry.load(state)] == ["m1"], \
        "a failed verdict must be held, not dropped -- the cursor has already moved past it"

    outage["on"] = False
    n = tick.topic_label("u", "slug", [], dry=False, state=state)
    assert n == 1, "the held message must be judged once the chain recovers"
    assert applied == [("m1", "Life/Health")]
    assert em_retry.load(state) == [], "a message that succeeded must leave the queue"
