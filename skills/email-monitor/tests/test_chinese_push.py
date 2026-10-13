#!/usr/bin/env python3
"""Synthetic checks for Chinese push-message formatting.

These cases verify that summaries stay readable and preserve the intended
message content when English input is rendered for a Chinese-language channel.
Use generated synthetic fixtures when extending these tests.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)

import em_alert  # noqa: E402
import em_tick  # noqa: E402


# ---------- secrets must not ride along in the gist ----------


def test_api_token_stripped_from_gist():
    out = em_alert.redact_push("token 9Xh2Kq7Lm4Zt 即将过期")
    assert "9Xh2Kq7Lm4Zt" not in out


# ---------- but the useful part must survive ----------


# ---------- Chinese must survive redaction ----------


# ---------- frame ----------


# ---------- the PII rule this repo must keep ----------
