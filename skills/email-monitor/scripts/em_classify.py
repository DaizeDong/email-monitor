#!/usr/bin/env python3
"""email-monitor classifier — three-tier hybrid importance pipeline.

Importance == behavioral probability (will this be acted on), not topic. Architecture:
high-recall cheap gate -> LLM only for the uncertain minority (ARCHITECTURE §2.2).

  L0  deterministic rules  (zero cost) : VIP -> ACTION; List-Unsubscribe + not-whitelisted ->
       NOISE candidate; thread I already replied to -> ACTION; marketing domain -> NOISE.
  L1  cheap scoring (no LLM)           : 5 offline signals -> weighted score -> per-user threshold.
  L2  LLM semantic (only the unsure)   : caller feeds sender+subject+snippet to the local model.
       This module exposes the deterministic L0+L1 decision and marks `needs_l2` when unsure.

Two orthogonal axes:
  priority (response obligation) : URGENT | ACTION | FYI | NOISE
  semantic label                 : bill notification marketing social calendar dealer support

Safe-fail: when unsure default FYI (park it) not NOISE (silent swallow). FN:FP target ~3.5:1.
Deterministic: same input -> same output (self-evolve signal #6). L0/L1 carry NO randomness.

Usage:
  echo '{"from":"a@b.com","subject":"x","list_unsubscribe":false}' | python em_classify.py \
      --rules rules.json [--json]
Stdlib only.
"""
import argparse
import json
import re
import sys

PRIORITIES = ["URGENT", "ACTION", "FYI", "NOISE"]
URGENT_WORDS = re.compile(
    r"\b(urgent|asap|immediately|action required|final notice|past due|overdue|"
    r"expire|expiring|deadline today|by end of day|eod|payment failed|suspended)\b", re.I)
ACTION_WORDS = re.compile(
    r"\b(please (reply|confirm|review|sign|complete|respond)|can you|could you|"
    r"let me know|waiting (for|on) your|need(s|ed)? your|rsvp|due |reminder)\b", re.I)
CALENDAR_WORDS = re.compile(r"\b(invite|meeting|appointment|calendar|scheduled|reschedule)\b", re.I)
BILL_WORDS = re.compile(r"\b(invoice|receipt|payment|statement|bill|charged|refund|balance due)\b", re.I)
MARKETING_WORDS = re.compile(
    r"\b(sale|% off|discount|deal|offer|coupon|newsletter|unsubscribe|limited time|"
    r"shop now|new arrivals|webinar)\b", re.I)
SOCIAL_WORDS = re.compile(r"\b(liked|commented|followed|mentioned|tagged|friend request)\b", re.I)
SUPPORT_WORDS = re.compile(r"\b(ticket|case #|support|complaint|order #|return|warranty)\b", re.I)
NOREPLY = re.compile(r"\b(no[-_.]?reply|do[-_.]?not[-_.]?reply|notifications?@|mailer@)\b", re.I)


def domain_of(addr):
    m = re.search(r"@([^>\s]+)", addr or "")
    return (m.group(1).lower() if m else "").strip(".>")


def glob_match(pattern, value):
    # simple '*' glob (case-insensitive)
    rx = "^" + re.escape(pattern.lower()).replace(r"\*", ".*") + "$"
    return re.match(rx, (value or "").lower()) is not None


def semantic_label(subject, frm):
    s = subject or ""
    if CALENDAR_WORDS.search(s):
        return "calendar"
    if BILL_WORDS.search(s):
        return "bill"
    if SUPPORT_WORDS.search(s):
        return "support"
    if MARKETING_WORDS.search(s):
        return "marketing"
    if SOCIAL_WORDS.search(s):
        return "social"
    return "notification"


def l0(msg, rules):
    """Deterministic high-confidence rules. Return (priority|None, reason)."""
    frm = (msg.get("from") or "").lower()
    dom = domain_of(frm)
    subj = msg.get("subject") or ""
    vip = rules.get("vip", [])
    perm_noise = rules.get("permanent_noise", [])
    overrides = rules.get("sender_priority_overrides", {})

    # permanent noise wins only over non-VIP
    for pat in perm_noise:
        if glob_match(pat, frm) or glob_match(pat, dom):
            # but VIP overrides permanent noise
            if not any(glob_match(v, frm) or glob_match(v, dom) for v in vip):
                return "NOISE", "permanent_noise:%s" % pat
    # explicit per-sender override
    for pat, pr in overrides.items():
        if pr in PRIORITIES and (glob_match(pat, frm) or glob_match(pat, dom)):
            return pr, "override:%s->%s" % (pat, pr)
    # VIP promote
    for v in vip:
        if glob_match(v, frm) or glob_match(v, dom):
            return "ACTION", "vip:%s" % v
    # thread I already replied to -> ACTION
    if msg.get("i_replied_thread"):
        return "ACTION", "i_replied_thread"
    # urgent keyword in subject -> URGENT (high confidence)
    if URGENT_WORDS.search(subj):
        return "URGENT", "urgent_keyword"
    # list-unsubscribe + noreply + not whitelisted -> NOISE candidate
    if msg.get("list_unsubscribe") and NOREPLY.search(frm):
        return "NOISE", "list_unsub+noreply"
    return None, "l0_uncertain"


def l1(msg):
    """Cheap weighted score from offline behavioral signals -> (score, needs_l2)."""
    sig = msg.get("signals", {})
    # signals expected 0..1; absent treated as 0
    w = {
        "sender_interaction_rate": 0.30,
        "replied_to_sender": 0.25,
        "subject_recency": 0.10,
        "star_archive_history": 0.15,
        "thread_participation": 0.20,
    }
    score = sum(w[k] * float(sig.get(k, 0) or 0) for k in w)
    subj = msg.get("subject") or ""
    if ACTION_WORDS.search(subj):
        score += 0.20
    if URGENT_WORDS.search(subj):
        score += 0.30
    score = max(0.0, min(1.0, score))
    return score


def classify(msg, rules):
    threshold = rules.get("l1_threshold_default", 0.6)
    acct = msg.get("account")
    th = rules.get("thresholds", {}).get(acct, threshold) if acct else threshold
    try:
        th = float(th)
    except (TypeError, ValueError):
        th = 0.6

    pr, reason = l0(msg, rules)
    label = semantic_label(msg.get("subject"), msg.get("from"))
    if pr is not None:
        return {"priority": pr, "label": label, "tier": "L0",
                "reason": reason, "score": None, "needs_l2": False}

    score = l1(msg)
    if score >= th + 0.15:
        return {"priority": "ACTION", "label": label, "tier": "L1",
                "reason": "score>=th+margin", "score": round(score, 3), "needs_l2": False}
    if score < th - 0.15:
        # clearly low. marketing/social/noreply -> NOISE; else safe-fail FYI
        frm = msg.get("from") or ""
        if label in ("marketing",) or (NOREPLY.search(frm) and label in ("social", "notification")):
            return {"priority": "NOISE", "label": label, "tier": "L1",
                    "reason": "low_score+marketing", "score": round(score, 3), "needs_l2": False}
        return {"priority": "FYI", "label": label, "tier": "L1",
                "reason": "low_score_safe_fyi", "score": round(score, 3), "needs_l2": False}
    # the uncertain band -> hand to L2 (LLM). Until decided, safe-fail FYI.
    return {"priority": "FYI", "label": label, "tier": "L1->L2",
            "reason": "uncertain_band", "score": round(score, 3), "needs_l2": True}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", help="path to merged rules JSON (global + personal layer)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rules = {}
    if a.rules:
        with open(a.rules, "r", encoding="utf-8") as f:
            rules = json.load(f)
    msg = json.loads(sys.stdin.read())
    out = classify(msg, rules)
    print(json.dumps(out, ensure_ascii=False) if a.json else out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
