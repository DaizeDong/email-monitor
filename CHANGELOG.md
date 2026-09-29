# Changelog

All notable changes to this project are documented here (Keep a Changelog style).

## [Unreleased]
### Fixed
- Model classification, topic labeling and quality review inherit installed llmcall routing,
  model, timeout and fallback policy in judge mode. Obsolete local overrides report migration
  errors; nonmodel subprocess deadlines retain their bounds.
- Public documentation and source comments describe behavior without mailbox repair histories
  or owner-specific notification consent. Gist redaction documentation states its pattern limits.
- **The tick now counts topic verdicts, not just successes.** `topic_labeled=N` cannot separate
  "the gate is calibrated and this mail genuinely has no label" from "the gate refuses everything"
  from "the model chain is down", and those need three different responses; `unsure` and `failed`
  both write nothing, so both were invisible. Each account now logs
  `topic verdicts judged=.. decided=.. unsure=.. failed=.. labels_added=..`, and only when there
  was something to judge, since a line of zeros every five minutes trains the reader to skip it.
  Covered by a test that drives one message into each of the three states, verified by poisoning
  the counter and watching the test go red.
- **Test isolation is installed before collection.** The root `conftest.py` redirects
  runtime paths into temporary storage and intercepts effects before importing test modules.
  This keeps synthetic failures out of operational logs even when modules resolve paths at import.

### Added
- **`reference/topic-labeling.md`, the missing home for the load-bearing gate.** Step 5 of the
  workflow table pointed at `reference/monitor-and-classify.md`, which documents watch, classify,
  alert and archive and says nothing about topic labeling: a reader loading the named shard to
  perform the step found none of the step's rules there. The mechanism (pre-gate ordering, why a
  sender-keyed rule can never settle a type label, the evidence gate, the three states, the
  `--add` creates-a-label hazard and the rename ordering it forces) now has one home, and the
  index points at it.
- **Topic labeling: an opt-in, add-only capability that decides what a new mail is about, not
  just how important it is.** Off by default (`topic_labeling.enabled: false` in `registry.json`);
  the resolved state is logged every tick alongside `archive=`, so a disabled or misconfigured
  capability is never a silent surprise. A deterministic sender/domain/list-id pre-gate (`em_topic.py`)
  settles the obvious cases without a model call; anything left goes to the model, which must quote
  a verbatim evidence span from the sender or subject for every label it proposes -- a label whose
  evidence does not occur in the input is dropped automatically, and a message with no clear
  evidence gets no label at all (omission over commission). The transport is the shared `llmcall`
  package, wrapped in `em_tick._make_transport` to map its never-raises falsy-Result contract onto
  the three states `judge` needs to tell apart: `decided`, `unsure` (a taxonomy problem), and
  `failed` (the model chain is down). Labels are written with `gmail-imap-label.py --add` only; the
  write path (`em_tick.topic_label`) is asserted structurally to never reach `--archive` -- adding a
  label and hiding a message are different decisions, and topic labeling must never remove
  `\Inbox`. The taxonomy, sender map, and allowed-label set (`rules/taxonomy.md`,
  `rules/sender_map.json`, `rules/labels.json`) are DATA, not code: documented in `CONFIG.md`, they
  live only in the private companion config and never in this public repo.
- `skills/email-monitor/tests/` is now run in CI (`.github/workflows/skill-tests.yml`), separate
  from the `pii-guard` security workflow so an ordinary test failure and a detected leak stay
  distinguishable signals. Tests marked `integration` (need a live private config and a working
  model transport) are excluded on the runner, which has neither, and are meant to run locally only.
- **`scripts/init_config.py` now stamps the topic labeling capability, and gets real test
  coverage.** A mutation probe found that nothing imports `init_config.py`, so a broken generator
  was invisible to the suite; that in turn had let the generator drift out of sync with the
  capability above -- it wrote `topic_labeling` nowhere in `registry.json` and stamped none of
  `rules/taxonomy.md`, `rules/sender_map.json` or `rules/labels.json`, so a fresh machine
  following `CONFIG.md` got a config directory missing everything the capability reads.
  `REGISTRY` now carries `topic_labeling.enabled: false`, matching `em_tick.py`'s inert default,
  and the generator stamps a synthetic skeleton for all three `rules/` files (all three are
  gitignored in the stamped companion repo; only the skeleton this generator produces is ever
  written by it). `skills/email-monitor/tests/test_init_config.py` runs the generator into a temp
  directory and asserts `em_topic.load_config` can consume exactly what it produced, plus `write()`
  force semantics, idempotent re-runs, and a `verify_config.py` pass/fail check.

### Fixed
- **A pre-gate sender-map hit was never checked against the account's own allowed-label set**, only
  the model's proposals were. `em_topic.judge` now checks pre-gate labels against the same
  allowed set, dropping unsupported labels with a `drop_reason`.
- **`TYPE_LABELS` was a hardcoded public guess at part of the private standard's spelling, and drift
  was silent.** `labels.json` may now carry an optional `_type_labels` override that lives with the
  standard it belongs to; when absent, `em_topic.load_config` still falls back to the module default
  but now logs a warning, naming the account, when that default does not occur in the account's own
  allowed set -- the case where the type/source split silently does nothing.
- **`em_tick._label_add` could count a phantom write.** It was written as `archive()`'s narrow
  sibling but omitted the matched-count check that function already carries: the label tool exits 0
  and prints "nothing to do" when its query matches no message. `_label_add` now parses the same
  `matched (\d+) messages` count and returns `False` on zero, so `topic_labeled=N` in the tick log
  can no longer count a label that was never applied.
- **"topic_labeling enabled but not configured" used to log as `enabled` and silently label nothing.**
  `em_tick.topic_label` now logs explicitly when it is reached (topic labeling is on) but
  `em_topic.load_config` returns `None` for that account, so "on but uninitialised" is never
  indistinguishable from "working".

## [0.2.0] - 2026-07-23
### Added
- **Appointment/deadline dates in mail now become *dated* reminders -- an optional email-monitor <->
  schedule-reminder co-op.** The agent classifier additionally extracts a `due_at` when an email states
  a concrete owner-facing date; `em_pool.upsert` already accepted `due_at` but `em_tick` never passed
  it. The extracted date is now set on the pool item so a dated obligation can produce
  a time-based reminder.
  Division of labour, no duplication with the pre-existing (but never-wired) `em_duenorm`: absolute/ISO
  dates are normalized in the new stdlib `em_dates.py` (time-preserving, rejects past/absurd); relative
  and English natural-language phrases ("by Friday", "August 3 at 3:45pm") are delegated to `em_duenorm`,
  resolved against the mail's own Date header. Unit-tested in `tests/test_em_dates.py`.
- **email-monitor now runs standalone (plug-and-play) without schedule-reminder.** The pool/reminder
  integration is an optional downstream: `em_pool.available()` gates every pool write and `preflight`
  no longer hard-requires `reminder.py`. With the base skill absent, email-monitor still watches,
  classifies and Discord-alerts (alert-only mode, logged each tick); with it present the two skills
  interoperate. Others can install email-monitor on its own.

### Security
- **Fixtures are generated from synthetic cases.** `tools/make_fixtures.py` emits public
  test inputs; the data-boundary gate compares committed output with a fresh generator run.
  Case-table review is still required to establish synthetic provenance.
- Data-boundary and PII checks run in hooks and CI. `.dataclass.json` seals runtime paths
  against forced adds; runtime records belong in a PRIVATE companion.

## [0.1.9] - 2026-07-13
### Changed
- **Alerts use the classifier's Chinese gist**, with coarse subject words as fallback.
  Priority framing, pool titles and the daily digest use Chinese text.
- `redact_push()` removes recognized addresses, URLs and token patterns. Names, dates,
  amounts and pure digit runs can remain; this is not complete de-identification or a
  guarantee that every credential is removed. Account display labels come from PRIVATE config.
### Fixed
- **A Chinese subject used to be erased entirely.** `redact_subject()` kept only ASCII, so every
  Chinese mail pushed the literal string `new mail` (and `em_summary` re-applied the same filter to
  pool titles). CJK now survives the subject redactor, which removes digit-bearing tokens.
- +14 regression tests (`tests/test_chinese_push.py`). Suite 155 -> 169.

## [0.1.8] - 2026-07-12
### Fixed
- **Archive lookup uses the RFC Message-ID.** Gmail's `rfc822msgid:` search does not accept
  its internal X-GM-MSGID. A zero matched count cannot establish successful archiving.
### Added
- **`archive.enabled` switch in `registry.json` (default `true`, preserving documented behavior).**
  With `false`, NOISE is still classified and tracked but is **never moved out of the INBOX**, for
  owners who want to review every message themselves. The tick logs `archive=enabled|DISABLED`
  every run and reports a `kept_in_inbox` counter, so "nothing is being archived" is never a
  silent surprise.
- +6 regression tests (`tests/test_archive_gating.py`): the query uses the RFC822 id, angle
  brackets are stripped, `matched 0` is a failure, `matched 1` is a success, the disabled switch
  never reaches `archive()`, and the absent key still defaults to enabled. Suite 149 -> 155.

## [0.1.7] - 2026-07-12
### Fixed
- **Archive credentials are scoped to the helper child.** `archive()` passes the resolved
  app password through that child's environment without modifying the parent's environment.
  Error diagnostics include child stderr so a failed helper is observable.
- +5 regression tests (`tests/test_archive_credentials.py`): the secret reaches the child, never
  reaches `os.environ`, does not wipe the inherited env, is not fabricated when absent, and a
  non-zero child still surfaces as `False` (never a silent success).

## [0.1.6] - 2026-07-09
### Changed
- Added model transport fallback and recorded the answering provider in each verdict.
  Transport policy now belongs to installed llmcall; local provider/model controls are obsolete.
### Reliability
- Failed model classification retains the deterministic heuristic fallback.

## [0.1.5] - 2026-07-09
### Changed
- Added model-based importance classification from sender, subject and bounded body.
  Current calls use installed llmcall in judge mode. `classifier.mode` selects model or
  heuristic classification; `owner` supplies task context.
- **`em_watch` now fetches the full message body** (`BODY.PEEK[]`, still no `\Seen`) and extracts
  best-effort plain text (prefers `text/plain`, strips `text/html`, skips attachments, caps at 50k
  chars) so the classifier has real content to read. Header-only fetches still yield `body=""`.
### Reliability
- Failed or invalid model output is logged and falls back to the deterministic heuristic.

## [0.1.4] - 2026-07-09
### Fixed
- **No more console-window flashing every tick (Windows).** Under the Task Scheduler the tick runs
  via `pythonw` (windowless), but each child process, `powershell` (resolve-cred, once per account),
  the label/archive tool, and the daily-summary worker, still popped a visible console window. All
  child `subprocess.run` calls pass `CREATE_NO_WINDOW` on Windows. The base reminder's
  notification subprocess uses the same behavior.

## [0.1.3] - 2026-07-06
### Fixed
- **register-task.ps1 heartbeat no longer dies after 24h.** The trigger used a fixed
  `RepetitionDuration (New-TimeSpan -Days 1)`, which silently stops the EmailMonitorTick heartbeat
  after one day, fatal for a monitor. Now uses a duration-less (indefinite) repetition so it runs
  every `IntervalMinutes` forever until removed.

## [0.1.2] - 2026-07-06
### Security (privacy red line)
- **Subject redactor hardening.** Removes recognized addresses, URLs, digit-bearing tokens
  and long opaque blobs. Ordinary names can remain and a short subject may survive unchanged.
  Title construction does not include the body. Tests cover the implemented pattern boundaries.

## [0.1.1] - 2026-06-27
### Changed
- **Configurable notification relay.** Alerts prefer the optional reminder relay when
  installed and otherwise use the configured standalone notifier.

## [0.1.0] - 2026-06-25
### Added
- Initial release. Thin orchestration skill: incremental IMAP watch, three-tier importance
  classifier, redacted Discord alerts, archive hook, schedule-reminder task pool integration,
  deadline normalizer, deterministic draft + AI-flavor linter, daily summary worker, EmailMonitorTick
  heartbeat template, and a 27-test program-judged acceptance suite.
