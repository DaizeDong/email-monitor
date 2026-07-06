# Changelog

All notable changes to this project are documented here (Keep a Changelog style).

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
