# Roadmap

Current: **v0.2.0**

## Current implementation and verification

The source includes explicit companion discovery, PRIVATE write admission, installed llmcall routing,
and evidence-gated topic labeling with per-verdict diagnostics. Configuration and storage checks
are separate from mailbox authentication, external delivery and process cleanup evidence. Synthetic/offline checks do not establish configured live readiness or a published release. See [CONFIG.md](CONFIG.md) and [CHANGELOG.md](CHANGELOG.md).

<a id="v020-historical-release-baseline"></a>
The v0.2.0 baseline added dated reminders from classifier-extracted `due_at` values normalized
by `em_dates.py`, optional schedule-reminder integration and generated fixtures checked by hooks
and CI. Monitoring, classification and alerts remain available without the reminder base.

<a id="v013-historical-acceptance"></a>
The [CHANGELOG](CHANGELOG.md) retains the v0.1.3 baseline and intervening releases: UID/UIDVALIDITY
watch with read-only BODY.PEEK and X-GM-MSGID deduplication, L0/L1 rules and an L2 hook, redacted
alerts and archive actions, idempotent thread-based pool entries, DST-correct NY-to-UTC deadlines,
draft linting, the daily-summary worker/heartbeat split and the original 27-test acceptance suite.
These historical checks are not a current live-readiness result.

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
