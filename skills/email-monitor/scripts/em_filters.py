#!/usr/bin/env python3
"""Compile sender_map.json into Gmail filter XML.

The filters keep the job they are good at: deterministic sender to label
mapping, evaluated by Google, working on a phone and while this machine is off.
They lose the job they were bad at, guessing a topic from keywords, and this
generator is structurally incapable of emitting such a rule: it only ever writes
`from:` criteria.

Generated filters never archive. Hiding a message is a separate decision.
The sender map and generated XML are DATA in verified PRIVATE Git companions;
both paths are checked before reading senders. Output publication is atomic.
"""
import argparse
import collections
import json
from pathlib import Path
import sys
from xml.sax.saxutils import quoteattr

import em_runtime

HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:apps="http://schemas.google.com/apps/2006">\n'
        '  <title>Mail Filters</title>\n')
TAIL = "</feed>\n"
MAXLEN = 3900     # a single from: clause stays comfortably inside Gmail's limit


def _domain_conflicts(sender_map):
    """Omit a broad positive when a narrower sender rule assigns another label."""
    domains = sender_map.get('by_domain') or {}
    narrower = [(address.rpartition('@')[2].casefold(), label)
                for address, label in (sender_map.get('by_address') or {}).items()]
    narrower.extend((domain.casefold(), label) for domain, label in domains.items())
    return {domain for domain, label in domains.items()
            if any(other_label != label and (other == domain.casefold()
                   or other.endswith('.' + domain.casefold()))
                   for other, other_label in narrower)}


def _groups(sender_map):
    by_label = collections.defaultdict(list)
    for addr, label in sorted((sender_map.get("by_address") or {}).items()):
        by_label[label].append(addr)
    conflicts = _domain_conflicts(sender_map)
    for dom, label in sorted((sender_map.get("by_domain") or {}).items()):
        if dom not in conflicts:
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
    frm = quoteattr("|".join(senders))
    return ('  <entry>\n'
            '    <category term="filter"></category>\n'
            '    <title>Mail Filter</title>\n'
            '    <content></content>\n'
            '    <apps:property name="from" value=%s/>\n'
            '    <apps:property name="label" value=%s/>\n'
            '  </entry>\n' % (frm, quoteattr(label)))


def uncompilable(sender_map):
    """What this compiler cannot express, so the coverage hole is visible.

    `by_list_id` entries are excluded on purpose: Gmail's `list:` operator does not
    match List-Id the way the kernel's pre-gate does, so a generated filter for them
    would disagree with the kernel about the same message. A missing rule is safer
    than a contradicting one, but it must be stated rather than inferred.
    """
    # Gmail applies every matching filter, while the kernel chooses the narrower
    # mapping. Overlapping conflicting domain positives therefore stay uncompiled.
    return {"by_list_id": len(sender_map.get("by_list_id") or {}),
            "by_domain_conflicts": len(_domain_conflicts(sender_map))}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sender-map", required=True)
    ap.add_argument("--out", required=True, help="XML path in a verified PRIVATE companion")
    a = ap.parse_args(argv)
    try:
        output = Path(em_runtime.prove_private(a.out)['path'])
        source = Path(em_runtime.prove_private(a.sender_map)['path'])
        with source.open(encoding='utf-8-sig') as handle:
            sm = json.load(handle)
        xml = compile_filters(sm)
        em_runtime.atomic_write(output, xml)
    except (OSError, ValueError, RuntimeError) as exc:
        print('filter export failed: '+str(exc), file=sys.stderr)
        return 1
    n = xml.count("<entry>")
    report = uncompilable(sm)
    print(json.dumps({"out": a.out, "entries": n, "uncompiled": report}))
    if n == 0:
        print("WARNING: sender map produced zero filters", file=sys.stderr)
    if report["by_list_id"]:
        print("NOTE: %d list-id rule(s) are NOT in this filter set; they apply only through the "
              "skill's own pre-gate, so they do not work on mobile or while this machine is off."
              % report["by_list_id"], file=sys.stderr)
    if report['by_domain_conflicts']:
        print('NOTE: %d conflicting domain rule(s) were not exported; narrower sender rules '
              'take precedence in the skill.' % report['by_domain_conflicts'], file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
