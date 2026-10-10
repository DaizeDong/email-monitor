# Durable delivery and recovery

A heartbeat has two separate facts: which messages were observed, and which actions
were confirmed. A moved IMAP cursor does not establish delivery. `process_account`
returns `planned`, `completed`, `incomplete` or `failed`; the CLI emits a final JSON
object containing `results` and the aggregate `status`. Required incomplete or failed
work causes a nonzero exit code.

## Account actions

Each `<storage.state_dir>/<slug>.state.json` has an `actions` mapping. Its keys include
the canonical account address, mailbox, UIDVALIDITY, RFC Message-ID and action.
Topic-label keys also distinguish the target label. `monitored_folders` chooses the
mailboxes, with INBOX as the compatible default. Duplicate or reordered fetches reuse
the same keys; pending work is processed even when the next fetch returns no messages.

The four account actions are `alert`, `pool`, `archive` and `topic_label`. A row holds
its key, complete scope, payload, status and receipt. Its lifecycle is:

1. `pending`: saved together with the observation cursor before any delivery.
2. `uncertain`: saved before the adapter is called.
3. `completed`: saved only after the adapter returns a matching confirmation.
4. `failed`: a matching `not_applied` receipt proves the action did not occur and can
   be retried using the same key. The failed checkpoint retains that receipt and its evidence.

A confirmation must contain `status: confirmed`, the dispatched `idempotency_key`,
the matching `adapter` and a nonempty verifiable `receipt_id`. A `not_applied` receipt
must instead provide nonempty `evidence`. Booleans, empty output, process exit code
zero and mismatched keys do not prove delivery or non-delivery. Label helper confirmations
additionally need structured JSON with a positive integer `matched` count and `applied: true`.
Matching non-delivery evidence is retained even when the helper exits unsuccessfully.

After an exception or ambiguous reply, the action stays uncertain. On a later tick,
`reconcile_action(action_record)` is consulted first. Matching confirmation only
checkpoints completion; matching non-delivery proof permits another attempt. Unknown
or malformed reconciliation causes no resend. The default reconciliation adapter
returns uncertain because legacy downstream helpers cannot query prior disposition.
This is an explicit limitation, not an exactly-once guarantee. An operator or future
adapter must obtain independent delivery evidence before resolving uncertainty.

Operator recovery after a helper defect is `em_catchup.py`. `alerts --before <ISO time
with offset>` gathers every account alert row and daily-summary alert step left
`uncertain` for mail dated before that time (pending rows belong to the running tick),
journals the catch-up in `catchup.state.json`, and sends them as ONE consolidated message
through the normal relay, most important first. Only a confirmed relay receipt marks the
members `completed`; each member receipt carries `delivery: "catch_up"`, the
`catchup_key` and the relay message ids. An interrupted catch-up is refused rather than
resent, and a rerun only re-marks members of a completed catch-up. `requeue --action
<adapter> --evidence <text>` turns uncertain rows of an idempotent adapter such as
`topic_label` into `failed` with a `not_applied` receipt so the next tick dispatches
them again; it refuses `alert` and `pool`. Neither archives mail or removes a label.

State writes are atomic and flushed before dispatch. A failed intent or uncertainty
checkpoint prevents the effect. A failed completion checkpoint leaves the earlier
uncertain row authoritative. Use one heartbeat writer per state directory; scheduling
uses IgnoreNew, while manually running concurrent writers is not covered by this
protocol's guarantees.

Legacy topic headers are preserved and keep the mailbox generation from their old
cursor. Missing scope, damaged queues or unrecognized legacy pending shapes fail
visibly without overwriting the file. Unresolved topic model work remains in
`topic_retry`; it is independent of completed alert, pool and archive actions.

## Dry execution

`--dry` prints the plan without creating directories, appending logs, saving cursors,
changing dedupe or retry state, or calling delivery adapters. Loaded caller-owned state
is copied before planning. Preflight and credential failures also return structured
failure without logging or sending alerts. The watcher receives its password through
a local argument; the caller's environment is preserved.

Dry means no persistent writes or delivery, not offline operation. The tick still resolves
credentials, fetches mail and classifies through its selected route. Default agent prompts
include body text and may use external llmcall transport; optional topic judgments may do so
with headers and taxonomy. Enforced local-only processing requires heuristic classification
and topics disabled before the dry run as well.

## Daily summary

The configured optional daily-summary worker remains reachable from the heartbeat.
It is deterministic and calls no model. Dry execution never starts it. The worker
checks the same PRIVATE storage boundary and keeps separate steps in
`summary.state.json`: alert delivery, marking the current event done, and arming the
next event. It checkpoints uncertainty before each step. An unconfirmed alert cannot
mark an event done. Proven `not_applied` steps retain their receipt and retry with the same
key; uncertain steps are not automatically replayed. A worker
failure, timeout or malformed completion report makes the heartbeat incomplete.

This separate summary workflow is outside the four account-action ledger and has
manual reconciliation only. The doctor reports its delivery as `not_measured`. Local
synthetic tests cover control flow and interruption handling; they do not prove live
Discord delivery, real reminder persistence or recovery on an installed scheduler.

## Rollout evidence

The doctor validates configuration, PRIVATE normal/linked worktrees, and a bounded
probe of the selected interpreter and llmcall dependency. It does not fetch mail,
notify anyone or write DATA. Offline fixtures exercise routing, storage, retries and
receipts without real mail or models. Controlled-account fetch/delivery/recovery,
scheduled-task registration/XML, heartbeat continuity and classifier/draft quality
still require separately authorized live measurements.
