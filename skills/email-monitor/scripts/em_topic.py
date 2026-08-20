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
_BARE = re.compile(r"([\w.\-+]+@[\w.\-]+)")


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


PROMPT = """You label one email by topic. Sender and subject only, never a body.

STANDARD (the only standard; do not invent labels or reinterpret these):
%(taxonomy)s

ALLOWED LABELS (copy byte for byte; anything else is discarded):
%(allowed)s

MESSAGE
From: %(frm)s
Subject: %(subj)s
Date: %(date)s

RULES
- Judge THIS message from ITS OWN sender and subject. Never reason "this sender
  is usually X so this one is X".
- Omission over commission: a missing label costs nothing, a wrong label is the
  defect being fixed. When the evidence is not plain, return an empty list.
- For every label you propose you MUST quote an "evidence" span copied VERBATIM
  from the From or Subject above. Not a paraphrase, not a summary. A label whose
  evidence cannot be found in the input is discarded automatically.

Reply with JSON only:
{"labels": [{"label": "<one of the allowed labels>", "evidence": "<verbatim span>"}]}
An empty list is a valid and often correct answer.
"""


def build_prompt(msg, taxonomy, allowed_labels):
    return PROMPT % {
        "taxonomy": taxonomy or "(no taxonomy configured)",
        "allowed": "\n".join("- %s" % l for l in allowed_labels),
        "frm": msg.get("from", ""),
        "subj": msg.get("subject", ""),
        "date": msg.get("date", ""),
    }


def _verdict(state, labels=None, dropped=None, reason=""):
    return {"state": state, "labels": labels or [], "dropped": dropped or [],
            "reason": reason}


def judge(msg, taxonomy, sender_map, allowed_labels, call=None, log=None):
    """Decide a message's topic labels, or decline.

    Three states, because "the call broke" and "the model judged but did not
    clear the bar" need different responses from the operator even though both
    write nothing to the mailbox.
    """
    mapped = pregate(msg, sender_map)
    if mapped:
        kept, dropped = verify_labels(mapped, msg)
        if kept:
            return _verdict("decided", kept, dropped, "sender map")
        return _verdict("unsure", [], dropped, "sender map evidence did not verify")

    if call is None:
        return _verdict("failed", reason="no transport supplied")

    try:
        reply = call(prompt=build_prompt(msg, taxonomy, allowed_labels))
    except Exception as exc:                     # transport is allowed to fail
        if log:
            log("topic judge transport failed: %s" % exc)
        return _verdict("failed", reason="transport error: %s" % exc)

    if not isinstance(reply, dict) or not isinstance(reply.get("labels"), list):
        return _verdict("failed", reason="unparseable model reply")

    allowed = set(allowed_labels)
    proposed, dropped = [], []
    for item in reply["labels"]:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        if label not in allowed:
            dropped.append({"label": label, "evidence": item.get("evidence"),
                            "source": "model",
                            "drop_reason": "label not in this account's allowed set"})
            continue
        proposed.append({"label": label, "evidence": item.get("evidence"),
                         "source": "model"})

    kept, ev_dropped = verify_labels(proposed, msg)
    dropped.extend(ev_dropped)
    if kept:
        return _verdict("decided", kept, dropped, "model")
    return _verdict("unsure", [], dropped, "no proposed label survived verification")
