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


def test_extract_json_plain():
    assert ac._extract_json('{"priority":"FYI","label":"x"}')["priority"] == "FYI"


def test_extract_json_codefenced():
    assert ac._extract_json('```json\n{"priority":"NOISE"}\n```')["priority"] == "NOISE"


def test_extract_json_embedded_in_prose():
    txt = 'Here is my answer: {"priority":"URGENT","label":"bill"} hope that helps'
    assert ac._extract_json(txt)["priority"] == "URGENT"


def test_extract_json_garbage_returns_none():
    assert ac._extract_json("no json here at all") is None
    assert ac._extract_json("") is None


def test_normalize_valid():
    out = ac._normalize({"priority": "action", "label": "Interview", "reason": "r", "confidence": 0.8}, {})
    assert out["priority"] == "ACTION"      # upper-cased
    assert out["label"] == "Interview"
    assert out["tier"] == "agent"
    assert out["score"] == 0.8


def test_normalize_bad_priority_rejected():
    assert ac._normalize({"priority": "SPAM"}, {}) is None
    assert ac._normalize({"label": "x"}, {}) is None       # no priority
    assert ac._normalize("not a dict", {}) is None


def test_normalize_defaults_label_and_bad_confidence():
    out = ac._normalize({"priority": "FYI", "confidence": "n/a"}, {})
    assert out["label"] == "notification"
    assert out["score"] is None


def test_build_prompt_contains_fields_and_json_contract():
    p = ac.build_prompt({"from": "a@b.com", "subject": "Hello", "body": "world",
                         "list_unsubscribe": True}, owner="Owner X")
    assert "a@b.com" in p and "Hello" in p and "world" in p
    assert "Owner X" in p
    assert "List-Unsubscribe header: yes" in p
    assert '"priority"' in p and "URGENT|ACTION|FYI|NOISE" in p


def test_build_prompt_truncates_long_body():
    big = "x" * (ac.BODY_CHARS + 5000)
    p = ac.build_prompt({"from": "a@b.com", "subject": "s", "body": big}, "")
    assert "[truncated]" in p
    assert len(p) < len(big) + 2000


def test_watch_extract_body_plaintext():
    raw = ("From: a@b.com\r\nSubject: s\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
           "Hello this is the body.\r\n")
    rec = em_watch.parse_header_fetch(raw, 1)
    assert rec["body"].strip() == "Hello this is the body."


def test_watch_extract_body_html_stripped():
    raw = ("From: a@b.com\r\nSubject: s\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
           "<html><body><p>Hi <b>there</b></p><script>bad()</script></body></html>\r\n")
    rec = em_watch.parse_header_fetch(raw, 1)
    assert "Hi" in rec["body"] and "there" in rec["body"]
    assert "bad()" not in rec["body"] and "<" not in rec["body"]


def test_watch_header_only_fetch_yields_empty_body():
    raw = "From: a@b.com\r\nSubject: s\r\n\r\n"
    rec = em_watch.parse_header_fetch(raw, 1)
    assert rec["body"] == ""
