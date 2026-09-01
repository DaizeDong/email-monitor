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

def test_load_returns_empty_for_missing_or_malformed():
    # A corrupt queue must degrade to empty, never raise: this queue exists to avoid
    # losing a message, and it must not become the thing that takes the tick down.
    assert em_retry.load({}) == []
    assert em_retry.load({"topic_retry": "not-a-list"}) == []
    assert em_retry.load({"topic_retry": [{"no_id": 1}]}) == []
    assert em_retry.load(None) == []


def test_enqueue_records_only_the_headers_judge_may_see():
    q = em_retry.enqueue([], rec("m1", subject="Hello"), "chain down")
    assert len(q) == 1
    e = q[0]
    # The kernel is handed From/Subject/Date/List-Id and nothing else. Storing a body
    # would let a retry judge on evidence a live pass never had.
    assert set(e) == {"from", "subject", "date", "list_id",
                      "message_id", "attempts", "last_error"}
    assert e["subject"] == "Hello"
    assert e["attempts"] == 0


def test_enqueue_is_idempotent_on_message_id():
    # The same message can fail again on a retry. Two copies would double every
    # future attempt and let one message burn the whole budget.
    q = em_retry.enqueue([], rec("m1"), "first")
    q = em_retry.enqueue(q, rec("m1"), "second")
    assert len(q) == 1
    assert q[0]["last_error"] == "second"


def test_record_without_message_id_is_not_queued():
    # Without an id the label could never be written, so queuing guarantees a retry
    # that cannot possibly succeed.
    seen = []
    q = em_retry.enqueue([], rec("", subject="No id"), "x", log=seen.append)
    assert q == []
    assert seen and "no message_id" in seen[0]


def test_queue_is_bounded_and_drops_oldest_first():
    q = []
    for i in range(em_retry.MAX_QUEUE + 5):
        q = em_retry.enqueue(q, rec("m%d" % i))
    assert len(q) == em_retry.MAX_QUEUE
    # Newest kept, oldest dropped: recent mail is what the operator still cares about.
    assert q[-1]["message_id"] == "m%d" % (em_retry.MAX_QUEUE + 4)
    assert all(e["message_id"] != "m0" for e in q)


def test_attempts_are_capped():
    e = em_retry._entry(rec("m1"))
    for _ in range(em_retry.MAX_ATTEMPTS - 1):
        assert em_retry.mark_attempt(e) is True
    # The last attempt returns False: a message failing for a reason retrying cannot
    # fix must eventually leave, or the queue never empties.
    assert em_retry.mark_attempt(e) is False
    assert em_retry.exhausted(e)


def test_store_bounds_and_round_trips_through_state():
    state = {}
    em_retry.store(state, [em_retry._entry(rec("m1"))])
    assert em_retry.load(state)[0]["message_id"] == "m1"


def test_to_record_round_trips_the_judge_input():
    r = rec("m1", subject="Round trip", frm="x@y.z")
    back = em_retry.to_record(em_retry._entry(r))
    for f in ("from", "subject", "date", "list_id", "message_id"):
        assert back[f] == r[f]


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


def test_unsure_is_not_retried(tick, monkeypatch):
    # `unsure` is a real verdict: the gate worked and the evidence did not support a
    # label. Retrying it would spend tokens to reach the same answer. Only `failed`,
    # which means no judgement happened at all, earns a retry.
    monkeypatch.setattr(tick, "_label_add", lambda *a, **k: True)
    import em_topic
    monkeypatch.setattr(em_topic, "judge",
                        lambda *a, **k: {"state": "unsure", "labels": []})
    state = {}
    tick.topic_label("u", "slug", [rec("m1")], dry=False, state=state)
    assert em_retry.load(state) == []


def test_queue_gives_up_after_the_attempt_cap(tick, monkeypatch):
    # A permanently unjudgeable message must not be retried forever.
    monkeypatch.setattr(tick, "_label_add", lambda *a, **k: True)
    import em_topic
    monkeypatch.setattr(em_topic, "judge",
                        lambda *a, **k: {"state": "failed", "labels": [], "reason": "down"})
    state = {}
    tick.topic_label("u", "slug", [rec("m1")], dry=False, state=state)
    for _ in range(em_retry.MAX_ATTEMPTS + 2):
        tick.topic_label("u", "slug", [], dry=False, state=state)
    assert em_retry.load(state) == [], "the queue must drain even when nothing ever succeeds"


def test_retries_do_not_touch_alert_archive_or_pool(tick, monkeypatch):
    # Those steps already ran for this message on its first pass. topic_label is the
    # only thing a retry may reach; re-alerting or re-archiving would be worse than
    # the missing label it is fixing.
    import inspect
    src = inspect.getsource(tick.topic_label)
    for forbidden in ("em_alert", "archive(", "em_pool"):
        assert forbidden not in src, \
            "topic_label reached %s; a retry would re-run it for already-processed mail" % forbidden
