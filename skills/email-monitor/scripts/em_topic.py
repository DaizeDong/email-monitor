#!/usr/bin/env python3
"""Topic labeling kernel: decide what a message is about, and refuse to decide
when the evidence does not support it.

Stage 3 of the pipeline lives here first because it is the load bearing gate.
A model's self reported confidence is least reliable exactly where it has the
least information. Requiring it to quote a span that actually occurs in the
input is a far harder thing to fake than a number, and it is checkable by a
string operation rather than by trusting the model about itself.
"""
import re

_WS = re.compile(r"\s+")


def normalize_span(s):
    """Fold case and collapse whitespace so quoting differences in spacing do
    not defeat an otherwise genuine citation."""
    if not s:
        return ""
    return _WS.sub(" ", str(s)).strip().lower()


def _haystack(msg):
    return normalize_span("%s %s" % (msg.get("from", ""), msg.get("subject", "")))


def evidence_holds(evidence, msg):
    """True iff `evidence` occurs literally (modulo case and whitespace) in the
    message's sender or subject."""
    needle = normalize_span(evidence)
    if not needle:
        return False
    return needle in _haystack(msg)


def verify_labels(proposed, msg):
    """Partition proposed labels into those whose evidence checks out and those
    whose does not. Dropping is silent to the mailbox but never silent to the
    log: each dropped label carries why."""
    kept, dropped = [], []
    for item in proposed or []:
        ev = item.get("evidence")
        if evidence_holds(ev, msg):
            kept.append(dict(item))
        else:
            d = dict(item)
            d["drop_reason"] = ("evidence span not found in From or Subject: %r"
                                % (ev if ev else "<empty>"))
            dropped.append(d)
    return kept, dropped
