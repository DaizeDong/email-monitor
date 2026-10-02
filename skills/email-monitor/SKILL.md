---
name: email-monitor
description: "Auto-monitor Gmail: classify new mail by importance, alert important ones, label mail by topic, draft concise replies, track tasks, daily summary."
---

# email-monitor, a thin orchestration skill for your inbox

> Governing principle (full text in `PHILOSOPHY.md`): **reuse, never rebuild; and a reply is never
> auto-sent.** Three substrates already exist on this machine (IMAP read/write toolchain, the
> schedule-reminder task pool, the Discord relay). email-monitor only adds the *new* seam: an
> incremental watch, a classify/draft orchestrator, and archive/summary hooks. It never builds a
> second store, scheduler, or notifier, and it never sends mail for you.

## When to use / when to stop

- **Use** for: monitoring Gmail inboxes, triaging new mail by importance, alerting on important ones,
  archiving noise, drafting concise replies for review, tracking the resulting tasks, daily digests.
- **Stop / route elsewhere**: bulk relabeling or one-off inbox cleanup -> the raw `gmail-imap-label.py`
  tool directly. Generic reminders unrelated to mail -> `schedule-reminder`. Sending mail -> the user,
  in Gmail, by hand (this skill only drafts).

## Hard rules (never violate)

1. **Never auto-send.** Replies land as Gmail drafts (`create_draft`) only; the user clicks Send.
2. **The pool is the schedule-reminder base.** Only `reminder.py <verb> --json` via subprocess; never
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

**The pool step is an OPTIONAL co-op (plug-and-play).** With schedule-reminder installed, email-monitor
tracks each actionable/FYI mail in its pool -- and, when the classifier extracts a concrete
appointment/deadline, as a *dated* reminder (absolute dates normalized by `em_dates.py`, time-preserving;
relative/English phrases like "by Friday" resolved by `em_duenorm.py` against the mail's own Date). With
schedule-reminder ABSENT, email-monitor continues watch, classify and configured mail actions, while
skipping the pool and daily summary. Its absence does not fail preflight (`em_pool.available()` gates
the heartbeat's pool and summary steps). So
the two skills interoperate when both are present, and each still stands alone.

## Config lives in a private companion repo

Account topology, classification rules, the VIP/kill lists, draft templates, and the controlled
project vocabulary are all externalized to a verified PRIVATE `email-monitor-config` Git repo, along
with versioned runtime state, pending payloads, receipts and logs (Mode B keeps credentials separate
in DPAPI). Relative storage paths resolve from that companion; there is no public-tool fallback. See
`reference/summary-and-deploy.md` for the registry schema.

Topic labeling (step 5) follows the same split: the taxonomy, sender map, and allowed-label set
(`rules/taxonomy.md`, `rules/sender_map.json`, `rules/labels.json`) are DATA and live only in that
private companion config, never in this public repo. See `CONFIG.md` for the schema. The skill
ships the method (evidence-gated labeling that never de-inboxes); the operator's actual taxonomy
is theirs alone.

## Progressive loading

This `SKILL.md` is the only always-loaded file. Read one `reference/<shard>.md` at a time, for the step
you are executing. Never load the whole `reference/` directory at once.
