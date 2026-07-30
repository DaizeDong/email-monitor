# Roadmap

Current: **v0.2.0**

## v0.2.0 (current)
- A concrete owner-facing date in a mail now becomes a *dated* reminder (`due_at` extracted by the
  classifier, normalized in `em_dates.py`, passed through to the pool).
- The skill runs standalone: the schedule-reminder pool integration is an optional downstream, so
  with the base skill absent email-monitor still watches, classifies and alerts.
- Test fixtures are generated rather than pasted, and the data boundary runs in both hooks and CI.
- v0.1.4 through v0.1.9 are not itemized here; see [CHANGELOG.md](CHANGELOG.md).

## v0.1.3
- Incremental IMAP watch (UID + UIDVALIDITY watermark, read-only BODY.PEEK, X-GM-MSGID dedupe).
- Three-tier classifier (L0 rules / L1 cheap scoring deterministic; L2 LLM hook).
- Redacted Discord alerts + archive via existing label tool.
- Task pool on the schedule-reminder base (idempotency + thread merge + ext namespace).
- Deadline normalizer (NY -> UTC, DST-correct).
- Draft compliance + AI-flavor linter (deterministic).
- Daily summary worker (due=signal / worker=content) + EmailMonitorTick heartbeat template.
- Program-judged acceptance suite (27 tests).

## Planned

The v0.2 slot was taken by the release above, which shipped different work. The labels below are a
backlog ordering, not version commitments.

- v0.2: real-IMAP IDLE-vs-poll reconnect/latency baseline + silent-stall watchdog end-to-end;
  classification golden-set expansion + few-shot kappa lift on hard classes.
- v0.3: draft template A/B (real dealer/support reply-rate); concept-drift detection on sender
  importance (e.g. a staffing portal becomes temporarily important during onboarding).
- v0.4: status-change monitoring (read/label/delete) via windowed UID re-fetch; encrypted state export
  for dual-machine sync (beyond the single-machine DPAPI constraint).
