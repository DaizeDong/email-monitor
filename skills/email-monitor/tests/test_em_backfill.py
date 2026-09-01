#!/usr/bin/env python3
"""Tests for the historical backfill of the deterministic sender map.

The dangerous operation here is not a crash, it is a confident bulk mislabel that
looks like it worked. So most of these assert a REFUSAL: the type-label guard, the
allowed-set filter, the additive-only posture, and dry-by-default. Each one is
written so that removing the guard it covers turns it red.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import em_backfill  # noqa: E402


MAP = {"by_address": {
    "a@bank.example": "Accounts/Bank",
    "b@bank.example": "Accounts/Bank",
    "c@dealer.example": "Life/Automotive",
    "d@nowhere.example": "Rutgers/RUNews",   # not in this account's allowed set
}}
ALLOWED = ["Accounts/Bank", "Life/Automotive"]
TYPES = ["Life/Receipt"]


def test_plan_groups_senders_by_label():
    by_label, skipped = em_backfill.plan(MAP, ALLOWED, TYPES)
    assert by_label == {
        "Accounts/Bank": ["a@bank.example", "b@bank.example"],
        "Life/Automotive": ["c@dealer.example"],
    }
    assert [s[0] for s in skipped] == ["d@nowhere.example"]


def test_a_label_outside_the_allowed_set_is_skipped_with_a_reason():
    # em_topic.judge would drop it, so writing it here would make the backfill
    # disagree with the live kernel. Skipping silently would be just as bad: a run
    # that covers less than the map describes must say so.
    _, skipped = em_backfill.plan(MAP, ALLOWED, TYPES)
    assert skipped == [("d@nowhere.example", "Rutgers/RUNews",
                        "not in this account's allowed set")]


def test_a_type_label_in_the_map_aborts_the_whole_run():
    # taxonomy.md: a type label is a property of the individual message, so a
    # sender-keyed rule can never settle one. A shop sends both order confirmations
    # and marketing from one address; backfilling Life/Receipt by sender would file
    # the marketing as a receipt. This must fail loudly, not skip quietly.
    bad = {"by_address": dict(MAP["by_address"], **{"shop@x.example": "Life/Receipt"})}
    with pytest.raises(em_backfill.UnsafeBackfill) as e:
        em_backfill.plan(bad, ALLOWED + ["Life/Receipt"], TYPES)
    assert "type label" in str(e.value)


def test_query_excludes_mail_that_already_has_the_label():
    # This is what makes a re-run a no-op and makes "matched N" mean "N messages
    # this run would change" rather than "N messages exist".
    q = em_backfill.build_query(["a@x.example", "b@x.example"], "Accounts/Bank")
    assert 'from:a@x.example OR from:b@x.example' in q
    assert '-label:"Accounts/Bank"' in q


def test_dry_is_the_default_and_passes_dry_to_the_tool():
    seen = {}

    def runner(args, env):
        seen["args"] = args
        class P:
            returncode = 0
            stdout = "matched 7 messages for query: x"
        return P()

    matched, ok = em_backfill.run_tool("u@x", "q", "Accounts/Bank", commit=False, runner=runner)
    assert (matched, ok) == (7, True)
    assert "--dry" in seen["args"]


def test_commit_drops_dry():
    seen = {}

    def runner(args, env):
        seen["args"] = args
        class P:
            returncode = 0
            stdout = "matched 3 messages for query: x"
        return P()

    em_backfill.run_tool("u@x", "q", "Accounts/Bank", commit=True, runner=runner)
    assert "--dry" not in seen["args"]


def test_never_removes_and_never_archives():
    # The kernel is add-only and so is this. --remove would delete curation the
    # operator did by hand; --archive would move historical mail out of the inbox,
    # which is a far bigger change than the missing label it is fixing.
    seen = {}

    def runner(args, env):
        seen["args"] = args
        class P:
            returncode = 0
            stdout = "matched 0 messages for query: x"
        return P()

    em_backfill.run_tool("u@x", "q", "Accounts/Bank", commit=True, runner=runner)
    assert "--remove" not in seen["args"]
    assert "--archive" not in seen["args"]
    assert "--add" in seen["args"]


def test_tool_failure_is_reported_not_raised():
    # One bad label must not abort the rest of the backfill.
    def runner(args, env):
        class P:
            returncode = 1
            stdout = ""
        return P()

    matched, ok = em_backfill.run_tool("u@x", "q", "L", commit=True, runner=runner)
    assert (matched, ok) == (0, False)


def test_real_config_has_no_type_label_in_the_map():
    """Guards the live config, not a fixture.

    If someone later maps a sender to Life/Receipt this fails here, at commit time,
    rather than in a bulk run against the real mailbox.
    """
    import json
    root = os.path.expanduser("~/.email-monitor-config/rules")
    if not os.path.isdir(root):
        pytest.skip("private config not present on this machine")
    with open(os.path.join(root, "sender_map.json"), encoding="utf-8") as fh:
        sm = json.load(fh)
    with open(os.path.join(root, "labels.json"), encoding="utf-8") as fh:
        lb = json.load(fh)
    types = set(lb.get("_type_labels", []))
    offenders = {a: l for a, l in sm.get("by_address", {}).items() if l in types}
    assert not offenders, "senders mapped to a type label: %r" % offenders
