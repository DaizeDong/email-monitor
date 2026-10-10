# Steps 1-2, Incremental watch, classify, alert, archive

## Incremental watch (`em_watch.py`, orchestrated by `em_tick.py`)

Each heartbeat connects read-only and reconciles a persisted watermark.

- The cursor is `(UIDVALIDITY, last_uid)` per account and mailbox. State, pending payloads,
  receipts and logs are versioned DATA in a verified PRIVATE Git companion. Never place them
  in this public tool or treat gitignore as a substitute for private storage.
- `SELECT(readonly=True)` and `BODY.PEEK[]` fetch the message without setting the Seen flag.
  The watcher extracts body text for classification; it is not a header-only fetch.
- The range is `UID (last_uid+1):*`, batch-capped. First use and UIDVALIDITY changes baseline
  at `UIDNEXT-1`; they do not backfill the whole mailbox. Ordinary later ticks resume the cursor.
- INBOX is the default. `monitored_folders` selects additional mailboxes. The durable identity
  binds canonical account, mailbox, UIDVALIDITY and RFC Message-ID; action keys also include
  the action and, for topic labels, the target label. Repeated fetches reuse those keys.
- Mailbox names are sent to SELECT and STATUS as IMAP quoted strings, so names with spaces or
  brackets such as `[Gmail]/All Mail` work. INBOX is read first. A message that appears in more
  than one monitored mailbox (every inbox message is also in All Mail) is handled once per
  account, in the first mailbox that showed it, and only recorded as observed in the others.
- Resolve credentials at runtime and pass the password to the watcher without putting it in
  argv or logs. Label helpers receive it in a child environment, without mutating the parent.

Observation and action completion are independent. A cursor can advance while an action remains
pending, failed or uncertain. Each account keeps its own state and result.

## Classification: agent by default, heuristic by configuration or fallback

`classify_record` defaults to `classifier.mode="agent"`. `em_agent_classify.py` sends sender,
subject and up to 12,000 body characters to the installed `llmcall` interface. Its current routing
policy chooses the provider, model, timeout and fallback. That transport may be external.
This is a text judgment call; the word "agent" in the classifier setting does not promise an
on-device model or a tool-using agent. The result supplies priority, semantic label, a short Chinese
gist and any extracted due date. Calls use `mode="judge"`. Remove obsolete local policy
overrides as described in `CONFIG.md`; the API, CLI and registry report migration errors.

With `classifier.mode="heuristic"`, or when the agent returns no valid verdict, `em_classify.py`
uses deterministic rules and scoring. VIP and sender rules, urgent keywords and bulk-mail signals
feed priorities `URGENT|ACTION|FYI|NOISE`. Its uncertain band returns FYI with `needs_l2=true`;
the heartbeat does not make a separate L2 call for that flag. Rule data comes from the companion.

For enforced local-only model processing, set `runtime.local_only=true`, select heuristic
classification and disable `topic_labeling.enabled`. Agent and topic routes fail before credential,
mail or model access because llmcall has no enforceable local-transport proof here. IMAP access
and explicitly configured notifications still connect to their services.

## Alert, archive and preview

URGENT/ACTION alert by default; `discord_push_levels` can select other priorities. `em_alert.py`
sends a redacted Chinese gist, falling back to coarse subject words. Redaction removes recognized
addresses, URLs and token patterns; names, dates, amounts and pure digit runs can remain. It does not
send the full body. Review this egress choice before enabling notifications.

NOISE is archived only when `archive.enabled` permits it. The label helper selects the RFC
Message-ID, adds the configured label and removes INBOX. A zero exit code alone proves nothing.
The alert, archive and topic adapters require matching receipts; trustworthy `not_applied`
evidence permits retry even if the helper exits nonzero. Malformed evidence, mismatched identity
and failed-exit delivery claims stay uncertain. See `delivery-state.md` for reconciliation.

`em_tick.py --dry` still validates configuration and storage, probes the interpreter, resolves
credentials, fetches mail and classifies through the selected route. It prints planned actions
without saving cursors, dedupe, retry state or logs, or calling delivery adapters. It does not run
the summary worker. Preflight failure prints structured failure and exits nonzero without an alert.

Immediate new-mail alerts come from this heartbeat. When the optional schedule-reminder base is
present, its own tick handles recurring reminders. When absent, pool tracking and daily summaries
are skipped; the configured watch, classify, alert and mailbox actions still operate.
