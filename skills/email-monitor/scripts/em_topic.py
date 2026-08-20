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
%(known)s
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


def build_prompt(msg, taxonomy, allowed_labels, known_source=None):
    known = ""
    if known_source:
        known = ("\nALREADY SETTLED, do not question it and do not repeat it: this message's source "
                 "label is %s. You are only deciding the labels listed below.\n" % known_source)
    return PROMPT % {
        "taxonomy": taxonomy or "(no taxonomy configured)",
        "allowed": "\n".join("- %s" % l for l in allowed_labels),
        "known": known,
        "frm": msg.get("from", ""),
        "subj": msg.get("subject", ""),
        "date": msg.get("date", ""),
    }


def _verdict(state, labels=None, dropped=None, reason=""):
    return {"state": state, "labels": labels or [], "dropped": dropped or [],
            "reason": reason}


# Labels that answer "what KIND of mail is this", as opposed to "who sent it".
# A sender-keyed rule can never settle these, so they always reach the model.
TYPE_LABELS = ("Receipt", "Promo")


def judge(msg, taxonomy, sender_map, allowed_labels, call=None, log=None,
          type_labels=TYPE_LABELS):
    """Decide a message's topic labels, or decline.

    The pre-gate contributes SOURCE labels only ("who sent this"). TYPE labels
    ("is this proof money already moved") are a property of the individual
    message, so a sender-keyed rule can never settle them: they always reach
    the model, even on a pre-gate hit. When the source is already known, the
    model is asked the smaller question of type labels only.

    Three states, because "the call broke" and "the model judged but did not
    clear the bar" need different responses from the operator even though both
    write nothing to the mailbox. A settled source label survives either way:
    "the source is settled but the model is unreachable" must not discard what
    the map already established.
    """
    mapped = pregate(msg, sender_map)
    source_kept, dropped = ([], [])
    if mapped:
        source_kept, dropped = verify_labels(mapped, msg)

    known = source_kept[0]["label"] if source_kept else None
    askable = [l for l in allowed_labels if l in type_labels] if known else list(allowed_labels)

    # Nothing left for the model to decide: the source is settled and this account
    # has no type labels enabled.
    if known and not askable:
        return _verdict("decided", source_kept, dropped, "sender map")

    if call is None:
        # A settled source label is still a real answer; a missing transport must not
        # discard what the map already established.
        if source_kept:
            return _verdict("decided", source_kept, dropped, "sender map, no transport for type labels")
        return _verdict("failed", reason="no transport supplied")

    try:
        reply = call(prompt=build_prompt(msg, taxonomy, askable, known_source=known))
    except Exception as exc:                     # transport is allowed to fail
        if log:
            log("topic judge transport failed: %s" % exc)
        if source_kept:
            return _verdict("decided", source_kept, dropped,
                            "sender map; type labels unavailable: %s" % exc)
        return _verdict("failed", reason="transport error: %s" % exc)

    if not isinstance(reply, dict) or not isinstance(reply.get("labels"), list):
        if source_kept:
            return _verdict("decided", source_kept, dropped,
                            "sender map; type labels unavailable: unparseable reply")
        return _verdict("failed", reason="unparseable model reply")

    allowed = set(askable)
    proposed = []
    for item in reply["labels"]:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        if label not in allowed:
            dropped.append({"label": label, "evidence": item.get("evidence"), "source": "model",
                            "drop_reason": "label not in the set this message was judged against"})
            continue
        if any(k["label"] == label for k in source_kept):
            continue                      # the map already settled it
        proposed.append({"label": label, "evidence": item.get("evidence"), "source": "model"})

    model_kept, ev_dropped = verify_labels(proposed, msg)
    dropped.extend(ev_dropped)
    labels = source_kept + model_kept
    if labels:
        return _verdict("decided", labels, dropped,
                        "sender map + model" if source_kept and model_kept
                        else ("sender map" if source_kept else "model"))
    return _verdict("unsure", [], dropped, "no proposed label survived verification")


import json
import os
import sys

SKILL = "email-monitor"


def _resolve_config_dir():
    """Locate the private companion config.

    An explicit override is AUTHORITATIVE, present or absent. datadir tries several
    candidates in order, which is right for discovery and wrong for an override: if the
    operator names a path and it is not there, the answer is "no config", not "I found a
    different one". Silently resolving elsewhere is how a test asserting inertness passes
    against a real config.

    realpath, not abspath: this skill is deployed through a symlink, and abspath would
    compute a tools/ path that does not exist in the deployed copy.
    """
    here = os.path.dirname(os.path.realpath(__file__))
    tools = os.path.abspath(os.path.join(here, "..", "..", "..", "tools"))
    if tools not in sys.path:
        sys.path.insert(0, tools)
    try:
        import datadir
    except Exception:
        return None
    for var in ("EMAIL_MONITOR_CONFIG_DIR", "EMAIL_MONITOR_CONFIG"):
        override = os.environ.get(var)
        if override:
            p = os.path.expanduser(override)
            if not os.path.isdir(p):
                return None
            try:
                datadir._reject_if_inside_own_repo(p, SKILL)
            except Exception:
                return None
            return p
    try:
        return datadir.resolve_data_dir(SKILL)
    except Exception:
        return None


def load_config(account_slug):
    """Return the topic config for one account, or None if this machine has not
    been initialised for topic labeling. Never raises, never falls back."""
    config_dir = _resolve_config_dir()
    if not config_dir:
        return None
    base = os.path.join(config_dir, "rules")
    tax_p = os.path.join(base, "taxonomy.md")
    map_p = os.path.join(base, "sender_map.json")
    lab_p = os.path.join(base, "labels.json")
    if not (os.path.isfile(tax_p) and os.path.isfile(map_p) and os.path.isfile(lab_p)):
        return None
    try:
        taxonomy = open(tax_p, encoding="utf-8").read()
        sender_map = json.load(open(map_p, encoding="utf-8"))
        labels = json.load(open(lab_p, encoding="utf-8"))
    except Exception:
        return None
    allowed = labels.get(account_slug)
    if not allowed:
        return None
    return {"taxonomy": taxonomy, "sender_map": sender_map, "allowed_labels": allowed}
