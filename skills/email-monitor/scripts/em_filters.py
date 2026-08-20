#!/usr/bin/env python3
"""Compile sender_map.json into Gmail filter XML.

The filters keep the job they are good at: deterministic sender to label
mapping, evaluated by Google, working on a phone and while this machine is off.
They lose the job they were bad at, guessing a topic from keywords, and this
generator is structurally incapable of emitting such a rule: it only ever writes
`from:` criteria.

Generated filters never archive. Hiding a message is a separate decision.
"""
import argparse
import collections
import json
import sys
from xml.sax.saxutils import escape

HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:apps="http://schemas.google.com/apps/2006">\n'
        '  <title>Mail Filters</title>\n')
TAIL = "</feed>\n"
MAXLEN = 3900     # a single from: clause stays comfortably inside Gmail's limit


def _groups(sender_map):
    by_label = collections.defaultdict(list)
    for addr, label in sorted((sender_map.get("by_address") or {}).items()):
        by_label[label].append(addr)
    for dom, label in sorted((sender_map.get("by_domain") or {}).items()):
        by_label[label].append("*@" + dom)
    return by_label


def compile_filters(sender_map):
    out = [HEAD]
    for label, senders in sorted(_groups(sender_map).items()):
        chunk, size = [], 0
        for s in senders:
            if size + len(s) + 1 > MAXLEN and chunk:
                out.append(_entry(label, chunk))
                chunk, size = [], 0
            chunk.append(s)
            size += len(s) + 1
        if chunk:
            out.append(_entry(label, chunk))
    out.append(TAIL)
    return "".join(out)


def _entry(label, senders):
    frm = escape("|".join(senders))
    return ('  <entry>\n'
            '    <category term="filter"></category>\n'
            '    <title>Mail Filter</title>\n'
            '    <content></content>\n'
            '    <apps:property name="from" value="%s"/>\n'
            '    <apps:property name="label" value="%s"/>\n'
            '  </entry>\n' % (frm, escape(label)))


def uncompilable(sender_map):
    """What this compiler cannot express, so the coverage hole is visible.

    `by_list_id` entries are excluded on purpose: Gmail's `list:` operator does not
    match List-Id the way the kernel's pre-gate does, so a generated filter for them
    would disagree with the kernel about the same message. A missing rule is safer
    than a contradicting one, but it must be stated rather than inferred.
    """
    return {"by_list_id": len(sender_map.get("by_list_id") or {})}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sender-map", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sm = json.load(open(a.sender_map, encoding="utf-8"))
    xml = compile_filters(sm)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(xml)
    n = xml.count("<entry>")
    report = uncompilable(sm)
    print(json.dumps({"out": a.out, "entries": n, "uncompiled": report}))
    if n == 0:
        print("WARNING: sender map produced zero filters", file=sys.stderr)
    if report["by_list_id"]:
        print("NOTE: %d list-id rule(s) are NOT in this filter set; they apply only through the "
              "skill's own pre-gate, so they do not work on mobile or while this machine is off."
              % report["by_list_id"], file=sys.stderr)


if __name__ == "__main__":
    main()
