# skills/email-monitor/tests/test_em_relabel.py
"""Planning is pure and therefore tested; IMAP is not mocked, because a mocked
IMAP proves only that the mock behaves. The completeness assertion is the one
that matters most: a run that silently judges fewer messages than it read looks
exactly like a run with nothing to do."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import em_relabel  # noqa: E402
import pytest  # noqa: E402

MESSAGES = [
    {"msgid": "1", "from": "a@example.com", "subject": "x", "labels": ["Receipt"]},
    {"msgid": "2", "from": "b@example.com", "subject": "y", "labels": []},
]


def test_complete_run_passes():
    verdicts = {"1": {"state": "decided", "labels": [{"label": "Receipt"}]},
                "2": {"state": "unsure", "labels": []}}
    em_relabel.assert_complete(MESSAGES, verdicts)


def test_missing_verdict_raises():
    with pytest.raises(em_relabel.IncompleteRun):
        em_relabel.assert_complete(MESSAGES, {"1": {"state": "unsure", "labels": []}})


def test_unsure_and_failed_produce_no_changes():
    verdicts = {"1": {"state": "unsure", "labels": []},
                "2": {"state": "failed", "labels": []}}
    plan = em_relabel.plan_changes(MESSAGES, verdicts)
    assert plan["add"] == {} and plan["remove"] == {}


def test_add_only_for_labels_not_already_present():
    verdicts = {"1": {"state": "decided", "labels": [{"label": "Receipt"}]},
                "2": {"state": "decided", "labels": [{"label": "Promo"}]}}
    plan = em_relabel.plan_changes(MESSAGES, verdicts)
    assert plan["add"] == {"Promo": ["2"]}


def test_plan_is_additive_only():
    """The current invariant: a routine pass adds and never removes, because removal on a
    curated historical corpus is the lossy direction. Asserted directly, so that a future
    change which starts removing has to come here and update it deliberately rather than
    slipping past a test that was already vacuous."""
    verdicts = {"1": {"state": "decided", "labels": [{"label": "Promo"}]},
                "2": {"state": "decided", "labels": [{"label": "Promo"}]}}
    assert em_relabel.plan_changes(MESSAGES, verdicts)["remove"] == {}


def test_system_labels_are_never_emitted_into_add():
    """This one can actually fail. If plan_changes stopped filtering SYSTEM_LABELS, a verdict
    that named the inbox label would produce an instruction to write it, and the invariant
    that this tool only ever adds topic labels would be broken silently."""
    msgs = [{"msgid": "9", "from": "a@example.com", "subject": "s", "labels": []}]
    verdicts = {"9": {"state": "decided",
                      "labels": [{"label": "\\Inbox"}, {"label": "Promo"}]}}
    plan = em_relabel.plan_changes(msgs, verdicts)
    assert "\\Inbox" not in plan["add"]
    assert plan["add"] == {"Promo": ["9"]}
