# Changelog

All notable changes to this project are documented here (Keep a Changelog style).

## [0.1.6] - 2026-07-09
### Changed
- **Classification now uses a cost-ordered provider chain, not a single model.** Each new mail is
  judged by the first provider that returns a parseable verdict, tried cheapest-first:
  `codex` (OpenAI Codex CLI `codex exec`, our least-used quota) -> `cc` (Claude Code headless via the
  hosted gateway, hosted inference) -> `claude` (plain Claude Code, direct
  Anthropic, full price). Configured via `classifier.chain` + `classifier.providers` in
  `registry.json`; the answering provider is recorded in the verdict's `tier`.
- Absolute binary paths + `.cmd`-via-`cmd /c` so it works under the minimal-PATH scheduled task.
  codex uses `-o <file>` for a clean final message (read-only sandbox, `--ephemeral`,
  `--skip-git-repo-check`); cc/claude use `--output-format json` (envelope unwrapped).
### Reliability
- Still fail-safe: a provider that is missing, times out, or returns garbage is skipped and the next
  is tried; if every provider fails, the tick falls back to the deterministic `em_classify` heuristic
  rather than going dark. All child processes keep `CREATE_NO_WINDOW`.

## [0.1.5] - 2026-07-09
### Changed
- **Classification is now agent-first: every new mail is judged by `claude -p` (headless).** The old
  path could only ever escalate on a literal urgent keyword in the *subject* — VIP promotion, thread-
  reply detection and the L1 behavioral signals were never wired in the live deployment, so real mail
  effectively never alerted. New `em_agent_classify.py` feeds sender + subject + full body to a Claude
  model (default `claude-opus-4-8`) and returns the response-obligation tier. Configured via a
  `classifier` block in `registry.json` (`mode`, `model`, `timeout_sec`, `owner`).
- **`em_watch` now fetches the full message body** (`BODY.PEEK[]`, still no `\Seen`) and extracts
  best-effort plain text (prefers `text/plain`, strips `text/html`, skips attachments, caps at 50k
  chars) so the classifier has real content to read. Header-only fetches still yield `body=""`.
### Reliability
- Agent classification is **fail-safe, never fail-silent**: if the `claude` CLI is missing, times out,
  or returns unparseable output, the tick logs it and falls back to the deterministic `em_classify`
  heuristic instead of going dark. All child processes keep `CREATE_NO_WINDOW` (no popup windows).

## [0.1.4] - 2026-07-09
### Fixed
- **No more console-window flashing every tick (Windows).** Under the Task Scheduler the tick runs
  via `pythonw` (windowless), but each child process — `powershell` (resolve-cred, once per account),
  the label/archive tool, and the daily-summary worker — still popped a visible console window. All
  child `subprocess.run` calls now pass `CREATE_NO_WINDOW` on Windows, so a 3-account tick no longer
  flashes 3 PowerShell windows every 5 minutes. Same fix applied to `em_alert.py`'s relay call.

## [0.1.3] - 2026-07-06
### Fixed
- **register-task.ps1 heartbeat no longer dies after 24h.** The trigger used a fixed
  `RepetitionDuration (New-TimeSpan -Days 1)`, which silently stops the EmailMonitorTick heartbeat
  after one day — fatal for a monitor. Now uses a duration-less (indefinite) repetition so it runs
  every `IntervalMinutes` forever until removed. (Found while deploying the skill live.)

## [0.1.2] - 2026-07-06
### Security (privacy red line)
- **Redactor hardening.** The outbound alert/summary title redactor leaked non-numeric PII — an
  adversarial review pushed emails, alphanumeric secrets (`hunter2`), and order/tracking/confirmation
  codes (`ABC123XYZ`, `1Z999AA10123456784`, `ABX7Q9`) through to Discord. `redact_subject` now also
  strips email addresses, URLs/bare domains, and **any alphanumeric token containing a digit** plus
  over-long blobs (shared by the immediate alert and the daily summary, so both egress points are
  fixed). Residual pure-alpha words / proper nouns may remain (they reach only the user's own private
  Discord); docstring/README claim corrected to be accurate. +9 regression tests
  (`tests/test_redaction_hardening.py`). Body and raw subject are still never egressed.

## [0.1.1] - 2026-06-27
### Changed
- **Discord egress unified through Agent Center relay**: pushes now prefer schedule-reminder's
  `relay.py send --stream mail` (per-stream identity in the Agent Center server) when the base
  is installed, and **fall back to the Big Brother relay (send.py) when it is not** — fully
  pluggable, no behaviour change when the base is absent. Existing env/arg overrides still win.

## [0.1.0] - 2026-06-25
### Added
- Initial release. Thin orchestration skill: incremental IMAP watch, three-tier importance
  classifier, redacted Discord alerts, archive hook, schedule-reminder task pool integration,
  deadline normalizer, deterministic draft + AI-flavor linter, daily summary worker, EmailMonitorTick
  heartbeat template, and a 27-test program-judged acceptance suite.
