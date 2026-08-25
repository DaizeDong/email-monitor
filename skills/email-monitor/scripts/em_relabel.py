#!/usr/bin/env python3
"""Batch topic labeling over historical mail. Judgement comes from em_topic;
this module only sequences, plans, and writes.

The additive posture is deliberate: this pass adds labels the kernel is
confident about and does not remove existing ones, because removal on a
historical corpus is the operation that can lose information the operator
curated by hand. Removal is available through the audited review flow, not as a
side effect of a routine pass.

TODO: the IMAP layer (locate messages by X-GM-MSGID, write a rollback
snapshot before any mutation, STORE grouped by label in batches under 1000,
and read back every touched message to assert its label set equals the
expected set) is deliberately not implemented here. It is ported from a
validated batch run that lives outside this repo against real mailbox data,
and pulling it in risks dragging real senders into a public repo. The CLI
entry point (--account, --since, --dry/--commit) depends on that layer and
is deferred along with it.
"""

SYSTEM_LABELS = {"\\Inbox", "\\Sent", "\\Draft", "\\Drafts", "\\Important",
                 "\\Starred", "\\Trash", "\\Junk", "\\Spam", "\\Muted", "\\Chat", "\\All"}


class IncompleteRun(Exception):
    """Raised when the verdict set does not cover every message that was read.
    A short run must fail loudly: silently judging fewer messages than were read
    is indistinguishable from a run that found nothing to do."""


def assert_complete(messages, verdicts):
    missing = [m["msgid"] for m in messages if m["msgid"] not in verdicts]
    if missing:
        raise IncompleteRun("%d of %d messages have no verdict (first: %s)"
                            % (len(missing), len(messages), missing[0]))


def plan_changes(messages, verdicts):
    """Turn verdicts into a label grouped plan. Only `decided` verdicts act."""
    add = {}
    for m in messages:
        v = verdicts.get(m["msgid"]) or {}
        if v.get("state") != "decided":
            continue
        have = set(m.get("labels") or [])
        for item in v.get("labels") or []:
            label = item["label"] if isinstance(item, dict) else item
            if label in SYSTEM_LABELS or label in have:
                continue
            add.setdefault(label, []).append(m["msgid"])
    return {"add": add, "remove": {}}
