#!/usr/bin/env python3
"""Unit tests for em_agent_classify pure helpers (no live `claude -p` call).

Covers the parts that decide correctness independent of the model: envelope/JSON extraction,
verdict normalization + validation, prompt assembly, and the em_watch body extractor. The live
classify() path is exercised by manual smoke tests, not here (it needs the CLI + network)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)

import em_agent_classify as ac  # noqa: E402
import em_watch                 # noqa: E402


def test_extract_json_garbage_returns_none():
    assert ac._extract_json("no json here at all") is None
    assert ac._extract_json("") is None
