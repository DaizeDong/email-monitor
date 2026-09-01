#!/usr/bin/env python3
"""A deferred queue for topic verdicts that failed because the model was unreachable.

The problem this solves. `em_topic.judge` returns one of three states, and two of
them write nothing for opposite reasons. `unsure` means the gate worked and the
evidence did not support a label: that is a verdict, and repeating it would just
burn tokens to reach the same answer. `failed` means the call itself broke -- the
whole llmcall provider chain was down, or the reply was unparseable -- so no
judgement was ever made. Today both are counted and then dropped on the floor.

Dropping `failed` loses the message permanently. `em_tick` advances the INBOX
cursor unconditionally after each account, so the next tick starts past those
UIDs and nothing ever looks at them again. An outage lasting one tick silently
costs every message that arrived during it.

Why not simply hold the cursor back. Because the cursor is not a topic-labeling
checkpoint; it is the shared watermark for the whole pipeline. Alerting,
archiving, the reminder-pool upsert and classification all key off the same
`records` list. Rewinding it to retry a label would re-alert, re-archive and
re-upsert every message in the batch. The retry has to be independent of the
cursor, which is what this module is.

What gets queued. Only `failed`. An entry holds exactly the four header fields
`judge` is allowed to see (From, Subject, Date, List-Id) plus the message id
needed to write the label later, so a retry judges precisely what the live tick
would have judged. Bodies are never stored because the kernel never reads them.

Where it lives. In the per-account runtime state file under
EMAIL_MONITOR_STATE_DIR, alongside the cursors -- outside both the public skill
repo and the private config repo. Entries contain real senders and subjects, so
they are DATA: they must never be written anywhere a repo could pick them up.

Two bounds, both deliberate. `MAX_ATTEMPTS` stops a message that fails for a
reason retrying cannot fix from being retried forever; on the last attempt it is
dropped and logged by id, because a queue that never empties is a leak. `MAX_QUEUE`
stops a long outage from growing the state file without limit; the OLDEST entries
are dropped first, since the newest mail is the mail the operator is most likely
to still care about. Both drops are logged; neither is silent.
"""
from __future__ import annotations

MAX_ATTEMPTS = 5
MAX_QUEUE = 500

_KEY = "topic_retry"

# The exact fields em_topic.judge is given. Keeping this list in one place means a
# queued retry and a live judgement cannot silently diverge in what they see.
_HEADER_FIELDS = ("from", "subject", "date", "list_id")


def _entry(record, verdict_reason=""):
    """Build a queue entry from a tick record. Unknown fields are stored as "" so a
    retry sees the same shape a live judgement would, never a missing key."""
    e = {f: record.get(f, "") or "" for f in _HEADER_FIELDS}
    e["message_id"] = record.get("message_id", "") or ""
    e["attempts"] = 0
    e["last_error"] = str(verdict_reason or "")[:200]
    return e


def load(state):
    """Return the queue from a state dict. Missing or malformed -> empty list.

    Malformed is treated as empty rather than raising: this queue is an
    optimisation over losing the message entirely, and it must never be the thing
    that takes a tick down.
    """
    q = (state or {}).get(_KEY)
    if not isinstance(q, list):
        return []
    return [e for e in q if isinstance(e, dict) and e.get("message_id")]


def store(state, queue):
    """Write the queue back into the state dict, newest-last, bounded."""
    state[_KEY] = list(queue)[-MAX_QUEUE:]
    return state


def enqueue(queue, record, reason="", log=None):
    """Add a failed record. Idempotent on message_id: a message already waiting is
    not duplicated, because the same message can fail on a retry as well as on its
    first pass and two copies would double every future attempt."""
    mid = record.get("message_id", "") or ""
    if not mid:
        # Without an id the label could never be written, so queuing it would
        # guarantee a retry that cannot succeed.
        if log:
            log("topic retry: record has no message_id; not queued (subject=%r)"
                % (record.get("subject", "")[:60],))
        return queue
    for e in queue:
        if e.get("message_id") == mid:
            e["last_error"] = str(reason or "")[:200]
            return queue
    queue = list(queue)
    queue.append(_entry(record, reason))
    dropped = len(queue) - MAX_QUEUE
    if dropped > 0:
        if log:
            for e in queue[:dropped]:
                log("topic retry: queue full (%d); dropping oldest msgid=%s subject=%r"
                    % (MAX_QUEUE, e.get("message_id"), e.get("subject", "")[:60]))
        queue = queue[dropped:]
    return queue


def to_record(entry):
    """Adapt a queue entry back to the record shape topic_label iterates."""
    r = {f: entry.get(f, "") for f in _HEADER_FIELDS}
    r["message_id"] = entry.get("message_id", "")
    return r


def mark_attempt(entry):
    """Count one attempt. Returns True while the entry may be retried again."""
    entry["attempts"] = int(entry.get("attempts", 0)) + 1
    return entry["attempts"] < MAX_ATTEMPTS


def exhausted(entry):
    return int(entry.get("attempts", 0)) >= MAX_ATTEMPTS
