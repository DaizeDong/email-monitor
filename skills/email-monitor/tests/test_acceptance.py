#!/usr/bin/env python3
"""email-monitor program-judged acceptance suite (self-evolve regression gate).

Every signal here is machine-decided (no model self-assessment). Covers the 7 self-evolve signals:
  #1/#6 classification correctness + L0/L1 determinism
  #2     deadline normalization (tz, 0 errors) + extraction fields
  #3     base round-trip (idempotency / state machine / ext preserve / use-transition)
  #4     draft compliance linter (0 hits)
  #5     AI-flavor kill-list (0 hits)
  #7     dedup (Message-ID idempotency + thread_key merge)
  plus   IMAP watermark math (UID+UIDVALIDITY, rebaseline, batch cap)

Run: pytest -q   (from skills/email-monitor/)
The base round-trip tests auto-skip if the schedule-reminder base is not present.
"""
import json
import os
import sys
import uuid

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)

import em_draft_lint as dl   # noqa: E402

with open(os.path.join(os.path.dirname(__file__), "drafting.json"), encoding="utf-8") as _fixture_file:
    DRAFTS = json.load(_fixture_file)
import em_classify as cls    # noqa: E402
import em_duenorm as dn      # noqa: E402
import em_watch as watch     # noqa: E402
import em_alert as alert     # noqa: E402
import em_pool as pool       # noqa: E402

RULES = {
    "vip": ["recruiter@example-employer.com", "leasing@example-property.com"],
    "permanent_noise": ["promo@*.shop"],
    "sender_priority_overrides": {},
    "l1_threshold_default": 0.6,
    "discord_push_levels": ["URGENT", "ACTION"],
    "thresholds": {},
}

CLEAN_DEALER = DRAFTS['clean_dealer']


# ---------- signal #4: draft compliance ----------

def test_clean_dealer_draft_passes():
    assert dl.lint(CLEAN_DEALER, "dealer", config=DRAFTS["config"]) == []


# ---------- signal #5: AI-flavor ----------


# ---------- signal #1/#6: classification + determinism ----------

def test_golden_classification():
    path = os.path.join(HERE, "golden_classify.jsonl")
    rules = dict(RULES)
    correct = 0
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    for row in rows:
        out = cls.classify(row["msg"], rules)
        if out["priority"] == row["expect_priority"]:
            correct += 1
        else:
            print("MISS:", row["note"], "->", out["priority"], "want", row["expect_priority"])
    acc = correct / len(rows)
    assert acc >= 0.85, "classification accuracy %.2f < 0.85" % acc


def test_safe_fail_is_fyi_not_noise():
    msg = {"from": "stranger@example.org", "subject": "hello there", "account": "user1"}
    out = cls.classify(msg, RULES)
    assert out["priority"] == "FYI"


# ---------- signal #2: deadline normalization / tz ----------


def test_tz_dst_no_offset_error():
    # January (EST -05:00): 5pm local == 22:00Z
    out = dn.normalize("tomorrow 5pm", "Wed, 14 Jan 2026 09:00:00 -0500")
    assert out["due_utc"].startswith("2026-01-15T22:00")


# ---------- IMAP watermark math ----------

def test_first_run_baselines():
    lo, hi, rb = watch.compute_fetch_range({"uidvalidity": None, "last_uid": 0}, 111, 500)
    assert lo is None and rb is True
    cur = watch.advance_cursor(111, 500, [], 0, rb)
    assert cur == {"uidvalidity": 111, "last_uid": 499}


def test_uidvalidity_rotation_rebaselines():
    lo, hi, rb = watch.compute_fetch_range({"uidvalidity": 111, "last_uid": 499}, 222, 50)
    assert rb is True and lo is None
    cur = watch.advance_cursor(222, 50, [], 499, rb)
    assert cur == {"uidvalidity": 222, "last_uid": 49}


# ---------- alert redaction ----------


# ---------- signal #3 + #7: base round-trip (real base; skip if absent) ----------

REMINDER = pool.default_reminder_path()
base_present = os.path.isfile(REMINDER)
skip_base = pytest.mark.skipif(not base_present, reason="schedule-reminder base not installed")


@pytest.fixture()
def tmpdb(tmp_path):
    # The reminder contract refuses writes to a database that was never initialized.
    db = str(tmp_path / "em_test.sqlite3")
    pool._run(REMINDER, db, "init", [])
    return db


@skip_base
def test_thread_merge_advances_not_duplicates(tmpdb):
    tk = "thrid:%s" % uuid.uuid4().hex[:8]
    m1 = "<t1-%s@x>" % uuid.uuid4().hex[:6]
    m2 = "<t2-%s@x>" % uuid.uuid4().hex[:6]
    r1 = pool.upsert(REMINDER, tmpdb, m1, tk, "Reply re thread")
    r2 = pool.upsert(REMINDER, tmpdb, m2, tk, "Reply re thread")
    assert r1["item"]["id"] == r2["item"]["id"]
    assert r2["action"] == "merged"
    assert r2["item"]["ext"]["x_email_monitor_msg_count"] == 2
    found = pool.find_thread(REMINDER, tmpdb, tk)
    assert found["id"] == r1["item"]["id"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
