# Step 3, Track the affair in the schedule-reminder pool

The schedule-reminder pool is optional. The heartbeat uses it only when `em_pool.available()` finds
the reminder script; without it, pool tracking and daily summaries are skipped. All access goes
through `reminder.py <verb>` and its JSON responses via `em_pool.py`, never SQL or base internals.
The database and action ledger are versioned DATA in a verified PRIVATE Git companion. Relative
`storage.db` paths resolve from that companion, and unverified destinations fail before mail access.

## Mail affair -> base item (RFC 5545 VTODO-isomorphic)

| base field | source |
|---|---|
| `kind` | needs my action (reply/do/promise) -> `task`; fait-accompli to attend/know (appointment/charge/invite) -> `event` |
| `title` | Chinese one-liner from `summary_zh`, or redacted subject words; no full body, but ordinary names and dates may remain |
| `description` | 0-3 lines context (who/what/constraint), minimal PII, never the full body |
| `due_at` | normalized deadline (UTC RFC3339) from `em_duenorm.py` |
| `state` | `pending`->`doing`(drafted)->`done`(replied/closed)->`blocked`(awaiting other)->`cancelled` |
| `priority` | iCal 0-9 (1 highest) = urgency x importance (URGENT=2, ACTION=4, FYI=7) |
| `tags` | `acct:<slug>` + semantic label + affair type |
| `project` | controlled dotted vocab (`Life.Home`, `Work.Acme`, ...) from the config repo |
| `source` | always `email-monitor` |

## ext namespace `x_email_monitor_*` (deep-merged, additive-only)

`message_id` (RFC5322 idempotency key) · `thread_key` (References/In-Reply-To root or Gmail thrid) ·
`account` · `uid` · `from` + `subject_raw` (local audit only) · `task_type` · `due_confidence` ·
`draft_id` (binds done to "user sent") · `label` · `archive_ref` · `msg_count` + `last_seen_msg_id`.

## Pipeline per new mail

1. The heartbeat plans a pool action for URGENT/ACTION/FYI mail. It derives title, kind, priority,
   tags and due time from the classifier result; there is no separate pool-extraction model call
   or implemented `needs-confirm` approval flow. Default agent classification includes body text
   in llmcall prompts and may use external providers. For local-only processing, select heuristic
   classification with topic models off, as described in `monitor-and-classify.md`.
2. Persist the scoped action key and payload before dispatch. Mark the action uncertain before
   calling `em_pool.upsert`; `--dry` only prints the plan and never calls the pool write adapter.
3. Match retained message identity, then the account-scoped thread or a reviewed cross-thread
   rule. Such rules require account, sender, exact subject, nonempty entity tokens and an expiry.
   Preserve consolidation aliases and manually maintained summaries. A message replay does not
   increment the count or reopen a finished obligation. New actionable mail can reopen an
   explicitly archived information event. `ERR_BUSY` uses bounded exponential backoff.
4. Complete the pool action only when the returned item confirms the same action key. Ambiguous
   results stay uncertain for reconciliation; a moved mail cursor cannot discard pending work.
5. For later manual workflow changes, use transition/done/block instead of `update` on state.
   The heartbeat does not detect a sent draft or automatically close its pool item. A caller
   must verify the user's action before marking it done.

## Dual idempotency gate

The heartbeat's action key binds account, mailbox, UIDVALIDITY, Message-ID and action. The pool
retains current and prior action keys so an older message can still return its confirmation after
later mail advances the thread. A replay of legacy mail can add this receipt binding without
changing the obligation. Direct callers without an action key use an account/thread key and
retained Message-IDs. Adapter locking covers the read/update sequence across local processes.
Account slugs remain compatible with reviewed rules; the current email address is retained as
`x_email_monitor_account_user`. The private pending payload includes original subject/body text
for reviewed entity matching. See `delivery-state.md` for retry rules.

The base owns the database format. Keep its working database on local storage inside the PRIVATE
companion and follow its consistent-snapshot procedure for backups; do not copy live WAL files
piecemeal. Private version control is required for runtime DATA, not replaced by an ignored folder.
