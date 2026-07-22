# Step 3, Track the affair in the schedule-reminder pool

email-monitor is the base's designated downstream #2. The personal-affairs memory pool **is** the
schedule-reminder base. All access is `python reminder.py <verb> --json` via subprocess (`em_pool.py`).
Never read the `.db`, build SQL, or import internals.

## Mail affair -> base item (RFC 5545 VTODO-isomorphic)

| base field | source |
|---|---|
| `kind` | needs my action (reply/do/promise) -> `task`; fait-accompli to attend/know (appointment/charge/invite) -> `event` |
| `title` | derived Chinese one-liner from the classifier's `summary_zh` (e.g. `需回复:雇主要你确认上月工时`), never the raw body; credentials stripped |
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

1. Compute `thread_key`; **merge before create**, `em_pool.find_thread` hit -> advance the existing
   item (merge ext, bump `msg_count`), never a new item (avoids affair explosion).
2. Structured extraction (single-mail LLM -> strict JSON): `task_type / title (redacted imperative) /
   description / due (em_duenorm) / priority / project (snapped to controlled vocab) / tags`.
3. **Confidence gate**: high-confidence + clearly actionable -> `add` as pending. med/low -> a
   `needs-confirm` suggestion (low priority) surfaced in the daily summary for one-tap user
   confirmation. Never auto-do anything.
4. Write: `em_pool.upsert(... idempotency_key=email-monitor:<Message-ID> ...)`; `ERR_BUSY` ->
   exponential backoff.
5. **State only via transition/done/block** (`update` on state -> `ERR_USE_TRANSITION`): drafted ->
   update `progress=30` + `draft_id`; user clicks Send (Sent detected / draft gone) -> `done`;
   awaiting other -> `block --reason`; their reply -> `blocked->doing`.

## Dual idempotency gate

Gate 1 = `idempotency_key = email-monitor:<Message-ID>` (base UPSERT, same mail -> same id). Gate 2 =
`thread_key` semantic merge (advance within a thread, not a new item). Both are regression-tested
(`test_message_id_idempotent_same_id`, `test_thread_merge_advances_not_duplicates`).

The base reminder tool owns its own local DB file (local NTFS only; never a sync drive, WAL +
sync corrupts). Backup via `sqlite3 VACUUM INTO`, never a raw copy of `.db/-wal/-shm`.
