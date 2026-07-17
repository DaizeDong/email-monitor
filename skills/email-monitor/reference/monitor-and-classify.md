# Steps 1-2, Incremental watch, classify, alert, archive

## Incremental watch (`em_watch.py`)

The monitoring spine is **heartbeat + reconciliation**, not a bare IDLE daemon (a dead socket that
still `select()`s silently drops mail for days). One tick connects read-only and reconciles a
persisted watermark.

- **Anchor = (UIDVALIDITY, last_uid)** per account+folder, stored in a gitignored state JSON. Never
  sequence numbers (EXPUNGE shifts them), never `SEARCH SINCE` (day granularity, server-local).
- **Read-only**: `SELECT(readonly=True)` + `BODY.PEEK[HEADER.FIELDS ...]` so `\Seen` is never set.
- **Range** = `UID (last_uid+1):*`, batch-capped. `UIDVALIDITY` rotation -> re-baseline to `UIDNEXT-1`
  (do not backfill the whole mailbox). First run also baselines to the tip (act only on mail arriving
  *after* setup).
- **Dedupe anchor = X-GM-MSGID** (64-bit, stable across folders/labels) -> a "seen" set prevents
  re-analysis on relabel/move.
- **Anchor INBOX**, not All Mail. **Strict per-account isolation**: one account failing never stops
  the others.

The pure functions `compute_fetch_range`, `advance_cursor`, `parse_header_fetch`, `compute_thread_key`
carry all the watermark logic and are unit-tested with a fake IMAP (`tests/test_acceptance.py`).

Credential: resolve the DPAPI `.cred` at runtime -> env `GMAIL_APP_PW` -> the watcher. Never argv.

## Classify (`em_classify.py`), three-tier hybrid

Importance == **behavioral probability** (will it be acted on), not topic. High-recall cheap gate ->
LLM only for the uncertain minority.

- **L0 deterministic rules** (zero cost): VIP -> ACTION; permanent-noise glob -> NOISE (VIP overrides);
  per-sender override; thread I replied to -> ACTION; urgent keyword -> URGENT; list-unsubscribe +
  noreply -> NOISE candidate. Only high-confidence verdicts; ambiguity falls through.
- **L1 cheap scoring** (no LLM): five offline behavioral signals (sender interaction rate, replied-to,
  subject recency, star/archive history, thread participation) -> weighted score vs per-account
  threshold.
- **L2 LLM** (only the uncertain band): the caller feeds `sender + subject + truncated snippet` (never
  full body) to the local session model, `temperature=0`, output whitelist-validated. `em_classify`
  returns `needs_l2=true` for that band; until L2 decides, it safe-fails to **FYI**.

Two orthogonal axes: priority `URGENT|ACTION|FYI|NOISE` (response obligation) + a semantic label
(bill/notification/marketing/social/calendar/dealer/support). **Safe-fail = FYI** (park it), never
NOISE (silent swallow); FN:FP target ~3.5:1. Global defaults ship with the skill; the personal layer
(VIP list, overrides, thresholds, permanent noise) lives in the config repo and is the correction
loop for user feedback.

## Alert + archive

- **Alert** (`em_alert.py`): only URGENT/ACTION, a **Chinese one-line gist** (`summary_zh`) with every
  credential stripped by `redact_push()`, via the Discord relay
  (numbers and order/case ids stripped, ASCII only). Never the body or raw subject.
- **No double-notify**: immediate "new important mail" pings come from the watch; recurring due/overdue
  reminders come from the base `tick`. email-monitor never starts its own recurring notifier.
- **Archive** NOISE: `gmail-imap-label.py --query rfc822msgid:<id> --add <label> --archive` (label +
  de-inbox), selecting the single message precisely by msgid.
