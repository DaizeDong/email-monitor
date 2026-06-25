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
import em_classify as cls    # noqa: E402
import em_duenorm as dn      # noqa: E402
import em_watch as watch     # noqa: E402
import em_alert as alert     # noqa: E402
import em_pool as pool       # noqa: E402

RULES = {
    "vip": ["recruiter@AcmeCorp.com", "leasing@ExampleResidence.com"],
    "permanent_noise": ["promo@*.shop"],
    "sender_priority_overrides": {},
    "l1_threshold_default": 0.6,
    "discord_push_levels": ["URGENT", "ACTION"],
    "thresholds": {},
}

CLEAN_DEALER = """Hi Sam,

I am looking to buy a 2026 Honda Civic Sport and I am ready to move this week.

Please send your best out-the-door price as one number: discounted selling price, minus rebates, plus all fees. I have my own financing, so quote price only.

I am contacting a few dealers within 50 miles and will go with the cleanest quote. If you send a written breakdown today, I can commit fast.

Thanks,
Daize Dong"""


# ---------- signal #4: draft compliance ----------

def test_clean_dealer_draft_passes():
    assert dl.lint(CLEAN_DEALER, "dealer") == []


@pytest.mark.parametrize("bad,frag", [
    (CLEAN_DEALER.replace("price only.", "price only — thanks."), "em-dash"),
    (CLEAN_DEALER.replace("one number", "“one number”"), "curly"),
    (CLEAN_DEALER.replace("Hi Sam,", "# Hi Sam,"), "markdown"),
    (CLEAN_DEALER.replace("Daize Dong", "Daize Dong, Rutgers"), "signature"),
    (CLEAN_DEALER.replace("financing", "financing (café)"), "non-ascii"),
    (CLEAN_DEALER + "\nSent via smtplib.sendmail()", "send"),
])
def test_dirty_drafts_rejected(bad, frag):
    viol = dl.lint(bad, "dealer")
    assert viol, "expected violations for %s" % frag


# ---------- signal #5: AI-flavor ----------

def test_ai_flavor_killlist_caught():
    txt = "Hi,\n\nI wanted to reach out to leverage our synergy and delve into next steps.\n\nThanks,\nDaize Dong"
    viol = dl.lint(txt, "business")
    joined = " ".join(viol)
    assert "kill-list" in joined


def test_dealer_line_cap_enforced():
    long = "Hi Sam,\n\n" + "\n".join("This is line number %d here." % i for i in range(15)) + "\n\nThanks,\nDaize Dong"
    viol = dl.lint(long, "dealer")
    assert any("line count" in v for v in viol)


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


def test_l0_l1_deterministic():
    msg = {"from": "x@y.com", "subject": "please confirm the order", "account": "user1",
           "signals": {"sender_interaction_rate": 0.5}}
    outs = {json.dumps(cls.classify(msg, RULES), sort_keys=True) for _ in range(10)}
    assert len(outs) == 1, "classifier not deterministic"


def test_safe_fail_is_fyi_not_noise():
    msg = {"from": "stranger@unknown.org", "subject": "hello there", "account": "user1"}
    out = cls.classify(msg, RULES)
    assert out["priority"] == "FYI"


# ---------- signal #2: deadline normalization / tz ----------

def test_explicit_date_high_confidence():
    out = dn.normalize("by 2026-07-01", "Wed, 25 Jun 2026 09:00:00 -0400")
    assert out["confidence"] == "high"
    assert out["due_utc"].startswith("2026-07-01")


def test_relative_friday_med_and_utc():
    out = dn.normalize("by friday 5pm", "Wed, 25 Jun 2026 09:00:00 -0400")
    assert out["confidence"] == "med"
    # Fri 2026-06-26 17:00 EDT (-04:00) == 21:00Z
    assert out["due_utc"].startswith("2026-06-26T21:00")


def test_no_phrase_soft_due_inferred():
    out = dn.normalize("", "Wed, 25 Jun 2026 09:00:00 -0400")
    assert out["confidence"] == "inferred"
    assert out["due_utc"] is not None


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


def test_normal_increment_range():
    lo, hi, rb = watch.compute_fetch_range({"uidvalidity": 111, "last_uid": 499}, 111, 505)
    assert (lo, hi, rb) == (500, 504, False)


def test_uidvalidity_rotation_rebaselines():
    lo, hi, rb = watch.compute_fetch_range({"uidvalidity": 111, "last_uid": 499}, 222, 50)
    assert rb is True and lo is None
    cur = watch.advance_cursor(222, 50, [], 499, rb)
    assert cur == {"uidvalidity": 222, "last_uid": 49}


def test_batch_cap():
    lo, hi, rb = watch.compute_fetch_range({"uidvalidity": 1, "last_uid": 0}, 1, 100000, max_batch=400)
    # last_uid 0 but uidvalidity set -> not first-run; normal range capped
    assert hi - lo + 1 == 400


def test_thread_key_prefers_thrid_then_ref():
    assert watch.compute_thread_key("<a@x>", "", "", gm_thrid="999") == "thrid:999"
    assert watch.compute_thread_key("<a@x>", "<root@x> <mid@x>", "", None) == "ref:<root@x>"
    assert watch.compute_thread_key("<a@x>", "", "<irt@x>", None) == "ref:<irt@x>"
    assert watch.compute_thread_key("<a@x>", "", "", None) == "ref:<a@x>"


# ---------- alert redaction ----------

def test_alert_redacts_numbers_and_ascii():
    t = alert.build_title("URGENT", "user1", "Payment failed on order 12345 for $99.50")
    assert all(ord(c) < 128 for c in t)
    assert "12345" not in t and "99" not in t
    assert t.startswith("[URGENT]")


# ---------- signal #3 + #7: base round-trip (real base; skip if absent) ----------

REMINDER = pool.default_reminder_path()
base_present = os.path.isfile(REMINDER)
skip_base = pytest.mark.skipif(not base_present, reason="schedule-reminder base not installed")


@pytest.fixture()
def tmpdb(tmp_path):
    return str(tmp_path / "em_test.sqlite3")


@skip_base
def test_message_id_idempotent_same_id(tmpdb):
    mid = "<test-%s@x>" % uuid.uuid4().hex[:8]
    tk = "ref:%s" % mid
    r1 = pool.upsert(REMINDER, tmpdb, mid, tk, "Reply to mail re thing", due_at="2026-06-30T12:00:00Z")
    r2 = pool.upsert(REMINDER, tmpdb, mid, tk, "Reply to mail re thing", due_at="2026-06-30T12:00:00Z")
    # same thread_key -> merged onto same id (gate 2); and base idempotency on key (gate 1)
    assert r1["item"]["id"] == r2["item"]["id"]


@skip_base
def test_ext_namespace_preserved(tmpdb):
    mid = "<ext-%s@x>" % uuid.uuid4().hex[:8]
    r = pool.upsert(REMINDER, tmpdb, mid, "ref:%s" % mid, "Reply to mail",
                    ext_extra={"account": "user1", "label": "EM/ACTION/bill"})
    ext = r["item"]["ext"]
    assert ext["x_email_monitor_message_id"] == mid
    assert ext["x_email_monitor_account"] == "user1"
    assert ext["x_email_monitor_label"] == "EM/ACTION/bill"


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


@skip_base
def test_state_machine_transition_and_done(tmpdb):
    mid = "<sm-%s@x>" % uuid.uuid4().hex[:8]
    r = pool.upsert(REMINDER, tmpdb, mid, "ref:%s" % mid, "Reply re thing")
    iid = r["item"]["id"]
    doing = pool.transition(REMINDER, tmpdb, iid, "doing", progress=30)
    assert doing["state"] == "doing"
    done = pool.mark_done(REMINDER, tmpdb, iid)
    assert done["state"] == "done" and done["progress"] == 100


@skip_base
def test_update_on_state_rejected(tmpdb):
    """The base forbids changing state via update (ERR_USE_TRANSITION). Confirm contract holds."""
    mid = "<ut-%s@x>" % uuid.uuid4().hex[:8]
    r = pool.upsert(REMINDER, tmpdb, mid, "ref:%s" % mid, "Reply re thing")
    iid = r["item"]["id"]
    with pytest.raises(pool.PoolError) as ei:
        pool._run(REMINDER, tmpdb, "update", ["--id", iid, "--set", "state=done"])
    assert ei.value.code in ("ERR_USE_TRANSITION", "ERR_BAD_FIELD", "ERR_BAD_INPUT")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
