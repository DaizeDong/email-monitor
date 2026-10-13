#!/usr/bin/env python3
"""Tests for the historical backfill of the deterministic sender map.

The dangerous operation here is not a crash, it is a confident bulk mislabel that
looks like it worked. So most of these assert a REFUSAL: the type-label guard, the
allowed-set filter, the additive-only posture, and dry-by-default. Each one is
written so that removing the guard it covers turns it red.
"""
import os
import sys


HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import em_backfill  # noqa: E402


ALLOWED = ["Accounts/Bank", "Life/Automotive"]


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
