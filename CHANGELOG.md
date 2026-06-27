# Changelog

All notable changes to this project are documented here (Keep a Changelog style).

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
