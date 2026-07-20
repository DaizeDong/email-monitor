# Step 5, Daily summary + deploy the heartbeat

## Daily summary (due = signal, worker = content, decoupled)

The base `tick` only emits a trigger line and cannot carry a body. So the summary is modeled as a
one-shot dated event, and email-monitor reads `due` (read-only) to learn it fired, then `em_summary.py`
assembles the plain-text digest and ships it via the relay.

- Arm: `reminder.py add --kind event --title 'Daily email summary' --due-at <local 08:00 -> UTC>
  --source email-monitor --idempotency-key email-monitor:daily-summary:<YYYY-MM-DD>
  --ext '{"x_email_monitor_kind":"daily-summary"}'`.
- Worker: pull active items, bucket into **Important open / Awaiting reply / Drafted pending Send /
  New tasks today**, relay (auto 1990-char chunking). Then `done` today's event and **re-arm
  tomorrow** by local-calendar recompute (never naive +24h - that drifts an hour across DST). v0.1 has
  no RRULE expansion, so the worker explicitly re-arms.

## Heartbeat task (EmailMonitorTick)

One OS task runs `em_tick.py --config <registry.json>` every PT2-5M (TimeTrigger Repetition with
NO RepetitionDuration; a fixed Duration like P1D silently STOPS the heartbeat after that window and
blanks NextRun, so the repetition is duration-less and indefinite, matching register-task.ps1;
`StartWhenAvailable=true`, `MultipleInstancesPolicy=IgnoreNew`, battery on).
**Command pinned to absolute** `pythonw.exe` + absolute script + WorkingDirectory (schtasks runs with a
minimal PATH; a PATH-resolved python silently half-runs). Each tick: (a) per account watch -> classify
-> alert/archive -> pool; (b) check `due` for the summary event and run the worker if fired.

A `register-task.ps1` template is under `scripts/`; install with the absolute pythonw path for this
machine. Heartbeat + reconciliation also recovers after sleep/shutdown (it backfills on the next tick),
unlike a bare calendar trigger that misses while the PC is off.

Preflight every tick: check pythonw / relay / `reminder.py` / `resolve-cred.ps1` exist; missing -> a
loud Discord alert + exit, never a silent half-run. A watchdog ("no successful poll in X minutes ->
alert") guards against silent stalls.

## Companion config repo (Mode B)

`registry.json` (committed, zero secrets) lists accounts:

    {
      "schema_version": 1, "machine": "<hostname>",
      "accounts": [{"slug": "user1", "user": "user1@example.com", "role": "primary",
                    "imap_host": "imap.gmail.com", "cred_path": "~/.local/secrets/gmail-user1.cred",
                    "monitored_folders": ["INBOX"], "label_scheme": "EM/{priority}/{semantic}"}],
      "daily_summary": {"enabled": true, "local_time": "08:00", "tz": "America/New_York"}
    }

`rules/` holds the global classification defaults + a merged personal layer; `templates/` the four
draft profiles; `secrets/*.env` + `*.cred` are **gitignored** (Mode B) - the real app passwords stay in
DPAPI `~/.local/secrets/gmail-<slug>.cred`, the repo keeps only pointers. New machine: re-capture each app
password (DPAPI ciphertext is machine-bound and does not travel). See the config repo's runbooks.

## Acceptance / regression

`tests/test_acceptance.py` is the program-judged gate (run `pytest -q`): classification accuracy
(>=0.85), L0/L1 determinism, deadline tz (0 errors), base round-trip (idempotency / state machine /
ext preserve / use-transition), draft compliance + AI-flavor (0 hits), dedup (Message-ID + thread
merge), and IMAP watermark math. Hand off to `self-evolve` for ongoing iteration against these signals.
