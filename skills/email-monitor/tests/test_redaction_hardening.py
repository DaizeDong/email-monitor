#!/usr/bin/env python3
"""Regression guard for the alert/summary redactor (v0.1.2 privacy hardening).

An adversarial review proved the old redactor leaked emails, alphanumeric secrets, and
order/tracking/confirmation codes to Discord. Each must now be stripped from the outbound title.
The redactor is shared by the immediate alert (em_alert.build_title) and the daily summary
(em_tick.derive_title -> em_alert.redact_subject), so fixing it here covers both egress points.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.abspath(os.path.join(HERE, "..", "scripts"))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import em_alert  # noqa: E402


def r(subject):
    return em_alert.redact_subject(subject).lower()


def test_confirmation_code_is_stripped():
    out = r("Confirmation code ABX7Q9 for login")
    assert "abx7q9" not in out
    assert "confirmation" in out
