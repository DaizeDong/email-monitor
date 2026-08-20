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

## Deferred from topic labeling

These were scoped out of the initial topic-labeling capability (evidence-gated add-only labels,
off by default) and are recorded here rather than dropped:

- **Provenance ledger with per-batch rollback.** A record of which labels were written when and by
  which taxonomy version, so a bad taxonomy revision or a bad model run can be undone as a unit
  instead of hand-picked message by message.
- **Frozen regression gating with a deliberate ambiguous stratum.** A held-out evaluation set that
  never grows or shrinks silently, including cases chosen specifically because their correct label
  is genuinely unclear, so a labeling change is graded against "does it handle the hard cases
  consistently" rather than only the easy majority.
- **A novelty gate measuring distance from a label's confirmed members.** Before accepting a new
  message under an existing label, compare it against that label's already-confirmed examples and
  flag it when it is an outlier, catching taxonomy drift before it reaches the mailbox.
- **Stratified audit with exact binomial intervals.** Periodic sampling of labeled mail, stratified
  by label and by pre-gate vs model source, with a proper exact confidence interval on the error
  rate per stratum rather than one pooled accuracy number that can hide a bad label behind a lot of
  easy ones.

## Planned

The v0.2 slot was taken by the release above, which shipped different work. The labels below are a
backlog ordering, not version commitments.

- v0.2: real-IMAP IDLE-vs-poll reconnect/latency baseline + silent-stall watchdog end-to-end;
  classification golden-set expansion + few-shot kappa lift on hard classes.
- v0.3: draft template A/B (real dealer/support reply-rate); concept-drift detection on sender
  importance (e.g. a staffing portal becomes temporarily important during onboarding).
- v0.4: status-change monitoring (read/label/delete) via windowed UID re-fetch; encrypted state export
  for dual-machine sync (beyond the single-machine DPAPI constraint).
