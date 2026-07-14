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


def test_email_address_is_stripped():
    out = r("Re: contract from john.doe@acme.com")
    assert "@" not in out and "acme" not in out and "doe" not in out
    assert "contract" in out  # generic word survives (usefulness)


def test_alnum_secret_is_stripped():
    out = r("password is hunter2 for login")
    assert "hunter2" not in out
    assert "password" in out and "login" in out


def test_alpha_numeric_order_code_is_stripped():
    out = r("Your ABC123XYZ has shipped")
    assert "abc123xyz" not in out and "abc" not in out.replace("shipped", "")
    assert "shipped" in out


def test_tracking_number_is_stripped():
    out = r("Tracking 1Z999AA10123456784 delivered")
    assert "1z999aa10123456784" not in out and "999" not in out
    assert "tracking" in out and "delivered" in out


def test_confirmation_code_is_stripped():
    out = r("Confirmation code ABX7Q9 for login")
    assert "abx7q9" not in out
    assert "confirmation" in out


def test_url_and_domain_stripped():
    out = r("see https://evil.example.com/x and acme.com now")
    assert "http" not in out and "example" not in out and "acme" not in out


def test_plain_numbers_and_orders_still_stripped():
    out = r("order 12345 total 99.50 USD")
    assert "12345" not in out and "99" not in out and "50" not in out


def test_clean_subject_survives():
    out = r("Payment failed please review your account")
    assert "payment" in out and "failed" in out and "review" in out


def test_build_title_format_and_no_leak():
    """Format is Chinese now (v0.1.9); the no-leak guarantee is unchanged and is the point."""
    t = em_alert.build_title("URGENT", "user1", "Re: invoice from a@example.com code XY7Z9")
    assert t.startswith("【紧急】user1:")
    assert "@" not in t and "xy7z9" not in t.lower()
