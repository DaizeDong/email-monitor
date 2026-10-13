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


def test_paraphrase_is_rejected():
    """The model paraphrasing instead of quoting is the common failure, and it
    must not pass. This is the negative control for the whole gate."""
    assert not em_topic.evidence_holds("a payment was processed", MSG)
