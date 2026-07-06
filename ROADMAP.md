# Roadmap

Current: **v0.1.3**

## v0.1.3 (current)
- Incremental IMAP watch (UID + UIDVALIDITY watermark, read-only BODY.PEEK, X-GM-MSGID dedupe).
- Three-tier classifier (L0 rules / L1 cheap scoring deterministic; L2 LLM hook).
- Redacted Discord alerts + archive via existing label tool.
- Task pool on the schedule-reminder base (idempotency + thread merge + ext namespace).
- Deadline normalizer (NY -> UTC, DST-correct).
- Draft compliance + AI-flavor linter (deterministic).
- Daily summary worker (due=signal / worker=content) + EmailMonitorTick heartbeat template.
- Program-judged acceptance suite (27 tests).

## Planned
- v0.2: real-IMAP IDLE-vs-poll reconnect/latency baseline + silent-stall watchdog end-to-end;
  classification golden-set expansion + few-shot kappa lift on hard classes.
- v0.3: draft template A/B (real dealer/support reply-rate); concept-drift detection on sender
  importance (e.g. a staffing portal becomes temporarily important during onboarding).
- v0.4: status-change monitoring (read/label/delete) via windowed UID re-fetch; encrypted state export
  for dual-machine sync (beyond the single-machine DPAPI constraint).
