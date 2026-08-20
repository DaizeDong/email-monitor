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


_ADDR = re.compile(r"<([^>]+)>")
_BARE = re.compile(r"[\w.\-+]+@[\w.\-]+")


def sender_address(frm):
    """Extract the bare address from a From header value."""
    if not frm:
        return ""
    m = _ADDR.search(frm) or _BARE.search(frm)
    return (m.group(1) if m else "").strip().lower()


def _list_identity(msg):
    raw = msg.get("list_id") or ""
    m = _ADDR.search(raw)
    return (m.group(1) if m else raw).strip().lower()


def pregate(msg, sender_map):
    """Resolve a message deterministically, or decline.

    Order is address, then domain, then list identity: the most specific
    statement about a sender wins. Returns None rather than an empty list when
    nothing matches, so a caller cannot confuse "mapped to nothing" with
    "not mapped".
    """
    if not sender_map:
        return None
    addr = sender_address(msg.get("from"))
    if addr:
        label = (sender_map.get("by_address") or {}).get(addr)
        if label:
            return [{"label": label, "evidence": addr, "source": "map"}]
        domain = addr.split("@")[-1]
        label = (sender_map.get("by_domain") or {}).get(domain)
        if label:
            return [{"label": label, "evidence": domain, "source": "map"}]
    lid = _list_identity(msg)
    if lid:
        label = (sender_map.get("by_list_id") or {}).get(lid)
        if label:
            return [{"label": label, "evidence": lid, "source": "map"}]
    return None
