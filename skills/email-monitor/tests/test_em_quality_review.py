#!/usr/bin/env python3
"""Tests for the monthly adversarial label review.

Most of these assert a REFUSAL or a distinction, because the ways this tool can
be worse than useless all look like success: running while disabled, reporting a
clean bill of health during an outage, sampling only the newest mail, or writing
to the mailbox. Each test names the failure it catches.
"""
import os
import sys


HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import em_quality_review as qr  # noqa: E402


# ---------- the switch ----------


# ---------- sampling ----------


# ---------- reviewer contract ----------


# ---------- read-only ----------

def test_fetch_never_writes():
    # The tool is invoked in --dry mode. Today's session settled why auto-remediation
    # is wrong here: the findings usually come from a bad sender map, and stripping
    # instances without fixing the map lets the next backfill recreate them.
    seen = {}

    def runner(args, env):
        seen["args"] = args
        class P:
            returncode = 0
            stdout = "matched 1 messages\n a@x.example | Subject here"
        return P()

    qr.fetch_labelled("u@x", "Accounts", 0, None, runner=runner)
    assert "--dry" in seen["args"]
    assert "--remove" not in seen["args"]
    assert "--archive" not in seen["args"]


# ---------- the two reviewer errors the first real run produced ----------
