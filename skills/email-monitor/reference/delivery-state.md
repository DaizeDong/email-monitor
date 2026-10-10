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
5. `message_gone`: a `topic_label` or `archive` row whose last three dispatches in a row each
   answered `not_applied` with `matched: 0` (the search found the message nowhere in the
   mailbox, for example after it was deleted). The row keeps that receipt and its
   `gone_checks` count, is never dispatched again and no longer counts as pending work. Any
   other answer restarts the count. Alerts and pool rows never end this way.

New mail is handled in chunks of `SAVE_EVERY` (5) messages. Each chunk is classified,
planned and saved together with the observation cursor just past its last message and the
identities of every fetched message up to it; its actions are then dispatched before the next
chunk is classified. A tick that ends part way through a fetch (killed by the task time limit)
therefore keeps every chunk it saved, and the next tick fetches from just after the last one.
Each open row is visited at most once per tick, whichever chunk's pass reaches it first, so a
`not_applied` row is retried by the next tick, as before. The topic retry queue is judged once,
before any new mail. A tick starts no new chunk and no new topic judgement once `TICK_BUDGET`
(40 minutes from process start) of the task's one hour limit is spent; the rest waits for the
next tick, and topic records not reached stay in `topic_retry` in order. Accounts run one after
another, and each gets an equal share of the time left when it starts (`account_deadline`), so a
backlogged account stops at its share and the accounts after it still read their mail every tick;
time a quiet account leaves unused passes to the ones after it. After the budget only the chunk
in progress (one classification, at most one topic judgement), its deliveries, a catch-up send and
the summary worker can still run, which is minutes, not the twenty left in the hour.

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
Both take the state directory's writer lock and exit 3 with the holder's role, pid and
start time while a tick holds it. Each member row is written by re-reading its state file,
changing that one row and saving, so a row another writer advanced is never reverted. A
daily summary in a catch-up is dated by its run's `scheduled_at` (recorded by the summary
worker for new runs) or, for older runs, by its pool event's due time.

## Backlog of old mail

An account that could not be read for a while (a failing mailbox, an expired credential)
resumes from its stored cursor, so its first good ticks observe days of mail at once. The
tick still classifies, labels and pools every message as usual, but an alert for a message
whose `Date` was already more than 12 hours old when the tick saw it is held as backlog
(`payload.backlog`, with the message's date, sender and subject in `payload.origin`) instead
of being pushed on its own. Once every monitored mailbox of the account has been read to its
tip, or once the oldest held alert has waited 6 hours (a backlog of thousands of messages takes
many ticks), the tick sends all held alerts of that account as ONE consolidated catch-up message
through the normal relay, most important first, journaled in `catchup.state.json` like an
`em_catchup.py alerts` catch-up. The members are saved `uncertain` before the send; only a
confirmed relay receipt marks them `completed` with `catch_up` receipts. A proven
non-delivery makes them `failed`, so a later tick sends them again as one message; any other
outcome leaves them `uncertain` with an unfinished journal entry, which no tick resends and
which `em_catchup.py` refuses until an operator has checked the channel. Undated mail is never
treated as backlog.

State writes are atomic and flushed before dispatch. A failed intent or uncertainty
checkpoint prevents the effect. A failed completion checkpoint leaves the earlier
uncertain row authoritative. There is one writer per state directory, enforced by
`<state_dir>/.writer.lock` (byte 0, released by the OS when the holder exits). A non-dry
tick holds it for its whole run and fails with `WriterBusy` without touching state when
another writer holds it; the summary worker the tick starts works under the tick's lock (it
checks that the lock is held and that its recorded holder is the tick that is its parent).
`em_catchup.py`, the standalone `em_watch.py` CLI and a summary worker started by hand take
the lock themselves and exit 3 while another writer holds it. Scheduling also uses IgnoreNew.

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
key; an uncertain alert step is not automatically replayed. The two pool steps are
reconciled against the pool on the next run: a mark-done whose event is closed (done, or
cancelled by the pool's own expiry) completes, one whose event is still open is retried; an
arm-next whose key an item carries completes, otherwise it is retried (the keyed add is an
upsert). An arm-next whose recorded time has already passed arms the next future slot
instead, so a missed summary is not sent late. A worker
failure, timeout or malformed completion report makes the heartbeat incomplete.

This separate summary workflow is outside the four account-action ledger; its alert step
has manual reconciliation only (em_catchup), its pool steps reconcile as above. The doctor reports its delivery as `not_measured`. Local
synthetic tests cover control flow and interruption handling; they do not prove live
Discord delivery, real reminder persistence or recovery on an installed scheduler.

## Rollout evidence

The doctor validates configuration, PRIVATE normal/linked worktrees, and a bounded
probe of the selected interpreter and llmcall dependency. It does not fetch mail,
notify anyone or write DATA. Offline fixtures exercise routing, storage, retries and
receipts without real mail or models. Controlled-account fetch/delivery/recovery,
scheduled-task registration/XML, heartbeat continuity and classifier/draft quality
still require separately authorized live measurements.
