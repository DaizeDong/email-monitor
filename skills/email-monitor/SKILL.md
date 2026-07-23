---
name: email-monitor
description: Auto-monitor Gmail: classify new mail by importance, Discord-alert important ones, archive, draft concise replies, track tasks, daily summary.
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
   Read-only `BODY.PEEK` (no `\Seen`), anchor INBOX, dedupe on `X-GM-MSGID`.
4. **Privacy is a red line.** Mail bodies are processed only by the local session model; never fed to
   any external web/API. Discord gets a redacted title only. The public repo stores no body, no PII.
   App passwords live in DPAPI (a private secrets dir, e.g. `~/.local/secrets`), never on argv / in logs / in git.
5. **Drafts are a compliance object.** Plain ASCII, no markdown, no em-dash, no curly quotes, no AI
   kill-list words, signature exactly `Daize Dong`. Enforced by `em_draft_lint.py`, not by vibes.
6. **Heartbeat, not a bare IDLE daemon.** One short OS task per tick (`EmailMonitorTick`); IDLE is an
   optional accelerant only, always backed by the reconciliation heartbeat.

## Workflow (thin, load the named reference shard only for the step you are on)

| # | Step | Load | Code |
|---|------|------|------|
| 1 | Incremental watch + classify each new mail | `reference/monitor-and-classify.md` | `em_watch.py`, `em_classify.py` |
| 2 | Alert important + archive noise | `reference/monitor-and-classify.md` | `em_alert.py`, `gmail-imap-label.py` |
| 3 | Track the affair in the pool (+ a *dated* reminder if the mail names a date) | `reference/memory-pool.md` | `em_pool.py`, `em_dates.py`, `em_duenorm.py` |
| 4 | Draft a reply (review-only) | `reference/drafting.md` | `em_draft_lint.py`, Gmail `create_draft` |
| 5 | Daily summary + deploy the heartbeat | `reference/summary-and-deploy.md` | `em_summary.py`, `em_tick.py` |

A full unattended cycle is `em_tick.py --config <registry.json>` (the OS task runs exactly this).
Manual one-shot: run the same with `--dry` to see what it would do without alerting/archiving.

**The pool step is an OPTIONAL co-op (plug-and-play).** With schedule-reminder installed, email-monitor
tracks each actionable/FYI mail in its pool -- and, when the classifier extracts a concrete
appointment/deadline, as a *dated* reminder (absolute dates normalized by `em_dates.py`, time-preserving;
relative/English phrases like "by Friday" resolved by `em_duenorm.py` against the mail's own Date). With
schedule-reminder ABSENT, email-monitor runs alert-only (watch + classify + Discord) and skips the pool;
it never preflight-fails on the base skill's absence (`em_pool.available()` gates every pool write). So
the two skills interoperate when both are present, and each still stands alone.

## Config lives in a private companion repo

Account topology, classification rules, the VIP/kill lists, draft templates, and the controlled
project vocabulary are all externalized to a private `email-monitor-config` repo (Mode B: secrets
gitignored, app passwords in DPAPI). The skill is the method; the config is the signal. See
`reference/summary-and-deploy.md` for the registry schema.

## Progressive loading

This `SKILL.md` is the only always-loaded file. Read one `reference/<shard>.md` at a time, for the step
you are executing. Never load the whole `reference/` directory at once.
