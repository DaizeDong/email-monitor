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
and arm tomorrow's event. Only a matching alert confirmation permits the event changes. A valid
`not_applied` receipt, including one from a nonzero helper exit, is retained and permits the same
key to retry. Uncertain alert delivery stops that run for manual reconciliation without a blind
resend; an uncertain mark-done or arm-next is reconciled against the pool on the next run.
After a confirmed mark-done step, the worker arms tomorrow at `daily_summary.local_time`, using
the America/New_York calendar across DST. See `delivery-state.md` for receipt and crash boundaries.

The standalone summary `--dry` validates config and PRIVATE storage, then prints the intended
step names without reading due items, assembling content, writing state or sending anything.
The heartbeat's `--dry` does not start a summary worker at all. Neither result proves delivery.

## Heartbeat task (`EmailMonitorTick`)

`scripts/register-task.ps1` selects the interpreter from `-Pythonw`, then `runtime.python`, then
the available Python command. It resolves the path and runs the configuration doctor with that
same interpreter before registration. The task uses absolute interpreter, script and config paths,
an explicit working directory and `--python` for helper consistency. Supply an interpreter that
can return the doctor's JSON output. A missing interpreter or failed doctor stops registration.

The task repeats every `IntervalMinutes` (default 5), with no repetition duration,
`StartWhenAvailable`, `IgnoreNew` and battery operation enabled. The next ordinary tick resumes
the stored cursor after downtime, subject to the first-run and UIDVALIDITY baseline rules. Keep
one writer per state directory; the task setting does not serialize separate manual invocations.

Each tick checks config, draft settings, storage privacy, interpreter imports, the label helper,
the configured standalone notifier file, and an explicit credential resolver if supplied. The
current preflight requires that notifier file even when a unified relay is available. Failure
prints a JSON error and exits nonzero without a Discord alert or persistent log write. The code
does not implement a watchdog or an automatic stalled-heartbeat alarm.

Before any mail access, choose routing: default agent classification includes sender, subject and
body text in llmcall prompts and may use external providers. `runtime.local_only=true` requires
heuristic classification and topic labeling disabled. It also rejects a custom summary worker,
whose model transport cannot be verified. This constraint covers model processing; IMAP and
configured notifications still use their services. A dry tick still fetches and classifies mail.

## PRIVATE companion and runtime DATA

Use a verified PRIVATE Git companion for registry, rules, templates and real runtime DATA.
The default storage paths `data/state`, `data/email-monitor.log` and `data/pool.db` resolve from
the selected companion directory, never the working directory. Overrides receive the same
PRIVATE check. Unversioned, public and unverified destinations fail without a source-tree fallback.
Keep cursors, action and summary ledgers, unresolved payloads, receipts and logs under version
control in that companion. Mode B credentials remain separate in DPAPI; the registry keeps their
pointers. See the repository's `CONFIG.md` for initialization, discovery and runtime settings.

## Verification before unattended use

Run the doctor and the synthetic business suite from the repository root. The suite checks routing,
dry behavior, cursor and action persistence, receipts, retries and draft constraints. Existing live
integration tests can report skipped when their required private configuration is absent; that is
not a live pass. Separately verify controlled-account fetch, destination delivery receipts, fault
recovery, registered task XML and sustained heartbeat execution before claiming unattended
readiness. Offline tests do not establish classifier or draft quality on real mail.
