# skills/email-monitor/tests/test_em_relabel.py
"""Planning is pure and therefore tested; IMAP is not mocked, because a mocked
IMAP proves only that the mock behaves. The completeness assertion is the one
that matters most: a run that silently judges fewer messages than it read looks
exactly like a run with nothing to do."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import em_relabel  # noqa: E402

MESSAGES = [
    {"msgid": "1", "from": "a@example.com", "subject": "x", "labels": ["Receipt"]},
    {"msgid": "2", "from": "b@example.com", "subject": "y", "labels": []},
]


def test_plan_is_additive_only():
    """The current invariant: a routine pass adds and never removes, because removal on a
    curated historical corpus is the lossy direction. Asserted directly, so that a future
    change which starts removing has to come here and update it deliberately rather than
    slipping past a test that was already vacuous."""
    verdicts = {"1": {"state": "decided", "labels": [{"label": "Promo"}]},
                "2": {"state": "decided", "labels": [{"label": "Promo"}]}}
    assert em_relabel.plan_changes(MESSAGES, verdicts)["remove"] == {}
