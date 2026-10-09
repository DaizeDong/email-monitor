---
name: email-monitor
description: "Auto-monitor Gmail: classify new mail by importance, alert important ones, label mail by topic, draft concise replies, track tasks, daily summary."
---

# email-monitor, a thin orchestration skill for your inbox

email-monitor adds incremental watch, classification, draft orchestration and archive/summary
hooks to the Gmail IMAP toolchain and Discord relay. The optional schedule-reminder base supplies
the shared task pool. Replies require user review and manual sending. Design rationale:
[PHILOSOPHY.md](../../PHILOSOPHY.md).

## When to use / when to stop

- **Use** for: monitoring Gmail inboxes, triaging new mail by importance, alerting on important ones,
  archiving noise, drafting concise replies for review, tracking the resulting tasks, daily digests.
- **Stop / route elsewhere**: bulk relabeling or one-off inbox cleanup -> the raw `gmail-imap-label.py`
  tool directly. Generic reminders unrelated to mail -> `schedule-reminder`. Sending mail -> the user,
  in Gmail, by hand (this skill only drafts).

## Hard rules (never violate)

1. **Never auto-send.** Replies land as Gmail drafts (`create_draft`) only; the user clicks Send.
2. **The pool is the schedule-reminder base.** Only `reminder.py <verb>` via subprocess with JSON responses; never
   read its `.db`, build SQL, or import internals. ext keys are namespaced `x_email_monitor_*`.
3. **Incremental correctness = UID + UIDVALIDITY.** Never sequence numbers, never `SEARCH SINCE`.
   Read-only `BODY.PEEK[]` (no `\Seen`), INBOX by default; durable keys bind account, mailbox,
   UIDVALIDITY and RFC Message-ID. Observation and action completion are separate checkpoints.
4. **Choose model routing before reading mail.** Default agent classification sends sender, subject
   and up to 12,000 body characters through installed llmcall; its current policy may use external
   providers. Model routing, timeout and fallback are configured in llmcall; obsolete local
   overrides are rejected (see `CONFIG.md`). `runtime.local_only=true` requires `classifier.mode="heuristic"` and topic models off;
   agent/topic modes fail because llmcall supplies no enforceable local-transport proof. This model
   constraint does not disable IMAP or configured notifications. Discord gets a redacted gist.
   The public repo stores no runtime DATA; keep it versioned in a verified PRIVATE companion.
   App passwords live in DPAPI (a private secrets dir, e.g. `~/.local/secrets`), never on argv / in logs / in git.
5. **Drafts are a compliance object.** Signature, language and style come from the private registry draft section.
   Use `em_draft_lint.py --config <registry.json>`; Chinese and Unicode are allowed by zh/any,
   en retains ASCII constraints, and no-auto-send checks always apply.
6. **Heartbeat, not a bare IDLE daemon.** One short OS task per tick (`EmailMonitorTick`); IDLE is an
   optional accelerant only, always backed by the reconciliation heartbeat.

## Workflow (thin, load the named reference shard only for the step you are on)

Before creating a reminder, compare its obligation with existing records across sources.
The pool adapter preserves account-scoped message identity and returns an existing result on
replay. Cross-thread merges require a reviewed rule with account, sender, exact subject,
nonempty entity/period tokens and an expiry. Keep those rules in the PRIVATE pool. Consolidation
aliases route later mail to the retained obligation; completed obligations remain completed.

| # | Step | Load | Code |
|---|------|------|------|
| 1 | Incremental watch + classify each new mail | `reference/monitor-and-classify.md` | `em_watch.py`, `em_agent_classify.py`, `em_classify.py` |
| 2 | Alert important + archive noise | `reference/monitor-and-classify.md` | `em_alert.py`, `gmail-imap-label.py` |
| 3 | Track the affair in the pool (+ a *dated* reminder if the mail names a date) | `reference/memory-pool.md` | `em_pool.py`, `em_dates.py`, `em_duenorm.py` |
| 4 | Draft a reply (review-only) | `reference/drafting.md` | `em_draft_lint.py`, Gmail `create_draft` |
| 5 | Topic-label each new mail (add-only, opt-in) | `reference/topic-labeling.md` | `em_topic.py`, `gmail-imap-label.py` |
| 6 | Daily summary + deploy the heartbeat | `reference/summary-and-deploy.md` | `em_summary.py`, `em_tick.py` |

A full unattended cycle is `em_tick.py --config <registry.json>` (the OS task runs exactly this).
Manual one-shot: `--dry` reads mail and classifies through the selected route, then prints a plan
without persistent writes or delivery. Preflight failures print JSON and exit nonzero without alerts.
Delivery is complete only after a matching receipt. Uncertain actions require reconciliation;
see `reference/delivery-state.md` for keys, retry behavior and live-readiness limits.

Pool integration is optional. When `em_pool.available()` finds schedule-reminder, the heartbeat
tracks actionable/FYI mail and uses extracted dates for reminders. `em_dates.py` preserves the time
in absolute dates; `em_duenorm.py` resolves relative English phrases against the mail's own Date.
Without the base, watch, classification and configured mail actions continue, while pool tracking
and daily summaries are skipped. Missing schedule-reminder does not fail preflight.

## Config lives in a private companion repo

Account topology, classification rules, the VIP/kill lists, draft templates, and the controlled
project vocabulary are all externalized to a verified PRIVATE `email-monitor-config` Git repo, along
with versioned runtime state, pending payloads, receipts and logs (Mode B keeps credentials separate
in DPAPI). Relative storage paths resolve from that companion; there is no public-tool fallback. See
[CONFIG.md](../../CONFIG.md) for discovery, initialization and the registry schema, and
[DATA.md](../../DATA.md) for preservation and retention.

Topic labeling uses the private taxonomy, sender map and allowed-label set
(`rules/taxonomy.md`, `rules/sender_map.json`, `rules/labels.json`). These are DATA governed by the
same boundary. The public skill supplies the evidence-gated, add-only method.

## Progressive loading

This `SKILL.md` is the only always-loaded file. Read one `reference/<shard>.md` at a time, for the step
you are executing. Never load the whole `reference/` directory at once.
