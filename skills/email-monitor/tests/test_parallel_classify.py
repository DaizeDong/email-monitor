#!/usr/bin/env python3
"""Unit tests for em_tick.classify_records_parallel: a tick's new mails are classified concurrently
but their verdicts come back in input order, and the fan-out degrades safely. No live provider call --
classify_record is monkeypatched."""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)

import em_tick  # noqa: E402

AGENT_CFG = {"mode": "agent", "max_parallel": 8}


def _msg(i):
    return {"from": "s%d@example.com" % i, "subject": "subj %d" % i, "account": "acct",
            "list_unsubscribe": False, "body": "b%d" % i}


def test_parallel_preserves_order(monkeypatch):
    """verdicts[i] must correspond to msgs[i] even though calls finish out of order."""
    def fake(msg, rules, agent_cfg):
        return {"priority": "FYI", "label": msg["subject"], "tier": "agent"}
    monkeypatch.setattr(em_tick, "classify_record", fake)
    msgs = [_msg(i) for i in range(6)]
    out = em_tick.classify_records_parallel(msgs, {}, AGENT_CFG)
    assert [v["label"] for v in out] == ["subj %d" % i for i in range(6)]


def test_parallel_actually_overlaps(monkeypatch):
    """The whole point: N slow classifications overlap instead of running back-to-back."""
    active = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def fake(msg, rules, agent_cfg):
        with lock:
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
        time.sleep(0.2)
        with lock:
            active["now"] -= 1
        return {"priority": "NOISE", "label": "x", "tier": "agent"}
    monkeypatch.setattr(em_tick, "classify_record", fake)
    msgs = [_msg(i) for i in range(4)]
    t0 = time.time()
    out = em_tick.classify_records_parallel(msgs, {}, AGENT_CFG)
    elapsed = time.time() - t0
    assert len(out) == 4
    assert active["peak"] >= 2      # genuinely concurrent
    assert elapsed < 0.6            # 4 x 0.2s serial would be 0.8s


def test_empty_returns_empty(monkeypatch):
    monkeypatch.setattr(em_tick, "classify_record", lambda *a: {"priority": "FYI", "label": "x", "tier": "agent"})
    assert em_tick.classify_records_parallel([], {}, AGENT_CFG) == []


def test_single_message_uses_serial_path(monkeypatch):
    calls = []
    def fake(msg, rules, agent_cfg):
        calls.append(msg["subject"])
        return {"priority": "FYI", "label": msg["subject"], "tier": "agent"}
    monkeypatch.setattr(em_tick, "classify_record", fake)
    out = em_tick.classify_records_parallel([_msg(0)], {}, AGENT_CFG)
    assert len(out) == 1 and out[0]["label"] == "subj 0"


def test_heuristic_mode_runs_serial(monkeypatch):
    """When the agent chain is off there is no slow call to overlap, so it stays serial (no pool)."""
    def fake(msg, rules, agent_cfg):
        return {"priority": "NOISE", "label": msg["subject"], "tier": "heuristic"}
    monkeypatch.setattr(em_tick, "classify_record", fake)
    out = em_tick.classify_records_parallel([_msg(0), _msg(1)], {}, {"mode": "off"})
    assert [v["label"] for v in out] == ["subj 0", "subj 1"]


def test_per_message_failure_falls_back_to_heuristic(monkeypatch):
    """One message raising must not sink the tick; it degrades to the heuristic verdict."""
    def fake(msg, rules, agent_cfg):
        if msg["subject"] == "subj 1":
            raise RuntimeError("boom")
        return {"priority": "FYI", "label": msg["subject"], "tier": "agent"}
    monkeypatch.setattr(em_tick, "classify_record", fake)
    monkeypatch.setattr(em_tick.em_classify, "classify",
                        lambda msg, rules: {"priority": "NOISE", "label": "fallback", "tier": "heuristic"})
    out = em_tick.classify_records_parallel([_msg(0), _msg(1), _msg(2)], {}, AGENT_CFG)
    assert out[0]["label"] == "subj 0"
    assert out[1]["label"] == "fallback" and out[1]["tier"] == "heuristic"
    assert out[2]["label"] == "subj 2"
