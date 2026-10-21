# Step 6, Daily summary and heartbeat deployment

## Optional daily summary

The schedule-reminder base is optional. The heartbeat starts the summary worker only when
`daily_summary.enabled` is true and the reminder script is available. Without the base, it skips
both pool tracking and summary work; it does not fail preflight because the base is absent.

Seed a one-shot base event with kind `event`, source `email-monitor`, a due time and
`x_email_monitor_kind="daily-summary"` in its ext metadata. `em_summary.py` reads the base's
`due` result, builds the digest from active pool titles, and sends it through the relay. The bundled
worker is deterministic and calls no model. Titles may contain private context even though they
exclude full bodies; the configured Discord destination receives that digest.

For each due event, `summary.state.json` records three steps: alert, mark today's event done,
and arm the next event. Only a matching alert confirmation permits the event changes. A valid
`not_applied` receipt, including one from a nonzero helper exit, is retained and permits the same
key to retry. Uncertain alert delivery stops that run for manual reconciliation without a blind
resend; an uncertain mark-done or arm-next is reconciled against the pool on the next run.
After a confirmed mark-done step, the worker arms the next `daily_summary.local_time` slot that
is still ahead (today's when the run happens before it, otherwise tomorrow's), using the
America/New_York calendar across DST. See `delivery-state.md` for receipt and crash boundaries.

The standalone summary `--dry` validates config and PRIVATE storage, then prints the intended
step names without reading due items, assembling content, writing state or sending anything.
The heartbeat's `--dry` does not start a summary worker at all. Neither result proves delivery.

## Heartbeat task (`EmailMonitorTick`)

The task is declared in the repository's `automation/tasks.json` and installed by a registration
tool that reads that manifest; this repository ships no installer. The declaration runs
`em_tick.py --config ${COMPANION_ROOT}/registry.json --resolve-cred ${COMPANION_ROOT}/scripts/resolve-cred.ps1`
under `pythonw.exe` from the skill's scripts directory, with `${COMPANION_ROOT}` written as
`%EMAIL_MONITOR_CONFIG%`, so no machine path is registered. Run `scripts/verify_config.py` against
the companion before installing; the tick's own preflight refuses an unready configuration.

The task repeats every 5 minutes, with no repetition duration, `IgnoreNew` and battery operation
enabled; the companion's `automation/tasks.local.json` may change the interval, the enabled state
or the console category on one machine. The next ordinary tick resumes
the stored cursor after downtime, subject to the first-run and UIDVALIDITY baseline rules. The task
setting does not serialize manual invocations; the state directory's writer lock does (see
`delivery-state.md`).

Each tick checks config, draft settings, storage privacy, interpreter imports, the label helper
(`scripts/gmail-imap-label.py`, shipped beside the tick), the configured standalone notifier file, and an explicit credential resolver if supplied. The
current preflight requires that notifier file even when a unified relay is available. Failure
prints a JSON error and exits nonzero without a Discord alert or persistent log write. The code
does not implement a watchdog or an automatic stalled-heartbeat alarm.

Before any mail access, choose routing: default agent classification includes sender, subject and
body text in llmcall prompts and may use external providers. `runtime.local_only=true` requires
heuristic classification and topic labeling disabled. It also rejects a custom summary worker,
whose model transport cannot be verified. This constraint covers model processing; IMAP and
configured notifications still use their services. A dry tick still fetches and classifies mail.

## PRIVATE companion and runtime DATA

Use a PRIVATE Git companion for registry, rules, templates and real runtime DATA.
The default storage paths `data/state`, `data/email-monitor.log` and `data/pool.db` resolve from
the selected companion directory, never the working directory. Every write destination, overrides
included, must resolve inside a Git worktree outside the public source tree; paths inside the source
tree or Git metadata are refused with no source-tree fallback. Repository visibility is not checked
at runtime; commit-time gates enforce the data boundary.
Keep cursors, action and summary ledgers, unresolved payloads, receipts and logs under version
control in that companion. Mode B credentials remain separate in a vault; the registry keeps their
`cv://` references. See the repository's `CONFIG.md` for initialization, discovery and runtime settings.

## Verification before unattended use

Run the doctor from the repository root. Then verify controlled-account fetch, destination delivery receipts, fault
recovery, registered task XML and sustained heartbeat execution before claiming unattended
readiness.
