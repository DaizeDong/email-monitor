"""The pre-gate must resolve mapped senders WITHOUT the model, and must decline
cleanly when nothing matches. Asserting on the return value is not enough: the
point of the gate is that the model is never called, so that is asserted too in
Task 3's integration test."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import em_topic  # noqa: E402

SENDER_MAP = {
    "version": 1,
    "by_address": {"noreply@shop.example.com": "Accounts/Shopping"},
    "by_domain": {"news.example.org": "Promo"},
    "by_list_id": {"users.lists.example.net": "Rutgers/RUNews"},
}


def test_address_match_wins():
    msg = {"from": "Shop <noreply@shop.example.com>", "subject": "Order shipped"}
    got = em_topic.pregate(msg, SENDER_MAP)
    assert got == [{"label": "Accounts/Shopping", "evidence": "noreply@shop.example.com",
                    "source": "map"}]


def test_domain_match_when_no_address_match():
    msg = {"from": "Digest <weekly@news.example.org>", "subject": "This week"}
    got = em_topic.pregate(msg, SENDER_MAP)
    assert got[0]["label"] == "Promo"
    assert got[0]["source"] == "map"


def test_list_id_match():
    msg = {"from": "Someone <a@example.net>", "subject": "Notice",
           "list_id": "Campus notices <users.lists.example.net>"}
    got = em_topic.pregate(msg, SENDER_MAP)
    assert got[0]["label"] == "Rutgers/RUNews"


def test_unmapped_sender_declines():
    msg = {"from": "Stranger <who@unknown.example.com>", "subject": "Hello"}
    assert em_topic.pregate(msg, SENDER_MAP) is None


def test_empty_map_declines():
    msg = {"from": "Shop <noreply@shop.example.com>", "subject": "Order shipped"}
    assert em_topic.pregate(msg, {}) is None


def test_pregate_evidence_survives_verification():
    """A mapped label must still satisfy the Task 1 gate, so the two stages
    cannot disagree about what counts as evidence."""
    msg = {"from": "Shop <noreply@shop.example.com>", "subject": "Order shipped"}
    kept, dropped = em_topic.verify_labels(em_topic.pregate(msg, SENDER_MAP), msg)
    assert len(kept) == 1 and not dropped


def test_sender_address_parsing():
    assert em_topic.sender_address("A B <x@example.com>") == "x@example.com"
    assert em_topic.sender_address("x@example.com") == "x@example.com"
    assert em_topic.sender_address("") == ""
