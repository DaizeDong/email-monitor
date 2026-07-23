# Changelog

All notable changes to this project are documented here (Keep a Changelog style).

## [Unreleased]
### Added
- **Appointment/deadline dates in mail now become *dated* reminders -- an optional email-monitor <->
  schedule-reminder co-op.** The agent classifier additionally extracts a `due_at` when an email states
  a concrete owner-facing date; `em_pool.upsert` already accepted `due_at` but `em_tick` never passed
  it, so every tracked mail was undated -- an appointment confirmation ("8月3日15:45") landed as a note
  with the date only in its title, never a time-based reminder. The date is now set on the pool item.
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
- **Test fixtures are now generated, so a real email cannot get into one.** The 2026-07 leak in this
  repo was `tests/golden_classify.jsonl`: it had been built by pasting real messages out of the inbox
  this skill reads. Scrubbing it fixed that one file and nothing else, the next agent writing a
  classifier test is still holding a real inbox, and copy-paste is still the cheapest move available.
  The fixture is now **output**: `tools/make_fixtures.py` holds a case table (which classifier path is
  pinned, and why) and emits the `.jsonl`; `tools/data_boundary.py` requires the committed file to be
  byte-identical to a fresh generator run. **A real record cannot be regenerated**, so pasting one in
  fails at commit time, even when it looks perfectly innocuous, which is the case a content scanner
  structurally cannot catch. Workflow: edit `CASES`, run `python tools/make_fixtures.py`, commit both.
  Never hand-edit the `.jsonl`.
- **The boundary is enforced, not just available.** `data_boundary.py` now runs in `.githooks/pre-commit`,
  `.githooks/pre-push` and CI, alongside `pii_guard`. The two answer different questions: `pii_guard`
  asks *"does this look private?"* (a sieve, it catches what it was taught); `data_boundary` asks
  *"could this have been generated?"* (provenance, no real record passes, however harmless it reads).
- Vendored `tools/datadir.py` + `tools/data_boundary.py` and declared `.dataclass.json`. Audit found
  **no** DATA-class file tracked here: the registry, IMAP watermarks and logs already resolve outside
  the repo (`~/.email-monitor-config/`, `~/.local/state/email-monitor/`), so `"data"` is legitimately empty.
- **`.gitignore` is advisory; the seal is not.** `registry.json`, `config.json` and the `rules/`
  personal layer (real accounts, real VIP senders, which are PII, and cred pointers) are now
  declared `data_sealed`. They have never been tracked in any commit, and this keeps it that way:
  `git add -f` walks straight through `.gitignore`, and an agent making a fresh clone "work out of
  the box" reaches for exactly that flag. Verified the seal blocks a forced add.

## [0.1.9] - 2026-07-13
### Changed
- **The push is now the classifier's Chinese gist, not a redacted keyword fragment.** The old line
  read `[ACTION] user1: Getting ready for your upcoming session`, indistinguishable from a routine
  reminder, while the *body* said the payment method was incomplete and would block the next
  charge. Tasks went unnoticed for days. The classifier already reads the full body, so it now also
  returns `summary_zh` and the push becomes
  `【待办】个人:订阅支付方式未填,下次扣款前要补`. Priority (`【紧急】/【待办】/【知悉】/【噪音】`), the mailbox
  label, the pool titles (`需回复:` / `待查看:`) and the daily digest (`📬 每日邮件汇总` / `待处理` / …)
  are all Chinese.
- **This deliberately relaxes the "never egress content" rule** (owner-approved 2026-07-13), so the
  new `redact_push()` pins down exactly how much may leave the machine: an email address, URL, or
  code/token/tracking number (a >=6 char run mixing letters and digits) is replaced with `(见邮箱)`,
  while **dates, amounts and names survive**, stripping those is precisely what made the old line
  useless. The mailbox's human label is PII and lives in the **private** companion config
  (`accounts[].display_zh`); this repo hardcodes no account name (a regression test enforces it,
  after an earlier draft of this change put the real slugs in `em_alert.py`).
### Fixed
- **A Chinese subject used to be erased entirely.** `redact_subject()` kept only ASCII, so every
  Chinese mail pushed the literal string `new mail` (and `em_summary` re-applied the same filter to
  pool titles). CJK now survives redaction; digits/secrets are still stripped.
- +14 regression tests (`tests/test_chinese_push.py`). Suite 155 -> 169.

## [0.1.8] - 2026-07-12
### Fixed
- **Archiving reported success while archiving nothing (phantom).** `archive()` built the Gmail
  query as `rfc822msgid:<gm_msgid>`, but `gm_msgid` is Gmail's **internal X-GM-MSGID**, while the
  `rfc822msgid:` operator only matches the **RFC822 `Message-ID` header**. The search therefore
  matched zero messages; the label tool printed `nothing to do` and exited **0**; `archive()` read
  rc=0 as success and the tick logged `archived=1`. Combined with 0.1.7's credential bug, the net
  effect is that **this skill had never actually archived a single message**, confirmed against the
  live mailbox, which contained 0 messages carrying any `EM/` label.
  - `archive()` now takes the RFC822 `Message-ID` (strips `<>`), and **treats `matched 0` as a
    failure**, so the `archived` counter can no longer lie about work it did not do.
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
- **NOISE archiving had been failing on every tick, silently, since the agent-first release.** The
  bulk label tool (`gmail-imap-label.py`) authenticates from `GMAIL_APP_PW` and exits 2 without it.
  The tick exported that variable only around the IMAP fetch and popped it in a `finally` **before**
  the record loop, but archiving happens *inside* that loop, so every archive child ran with no
  password and died with `rc=2 / ERROR no GMAIL_APP_PW`. `archive()` now takes the resolved
  `app_pw` and injects it into **that child's env only**.
  - The secret is deliberately **not** put back into `os.environ`: the same loop spawns the
    classifier CLIs (codex / cc / claude), and leaking the Gmail app password into an LLM
    subprocess's environment would be a genuine secret-egress bug.
  - The archive-failure log line now includes the child's **stderr**. The old line printed only
    `rc=2` with no reason, which is exactly why this stayed invisible for days.
- +5 regression tests (`tests/test_archive_credentials.py`): the secret reaches the child, never
  reaches `os.environ`, does not wipe the inherited env, is not fabricated when absent, and a
  non-zero child still surfaces as `False` (never a silent success).

## [0.1.6] - 2026-07-09
### Changed
- **Classification now uses a cost-ordered provider chain, not a single model.** Each new mail is
  judged by the first provider that returns a parseable verdict, tried cheapest-first:
  `codex` (OpenAI Codex CLI `codex exec`, our least-used quota) -> `cc` (Claude Code headless via a
  hosted gateway) -> `claude` (plain Claude Code, direct). Configured via `classifier.chain` +
  `classifier.providers` in
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
  path could only ever escalate on a literal urgent keyword in the *subject*, VIP promotion, thread-
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
  via `pythonw` (windowless), but each child process, `powershell` (resolve-cred, once per account),
  the label/archive tool, and the daily-summary worker, still popped a visible console window. All
  child `subprocess.run` calls now pass `CREATE_NO_WINDOW` on Windows, so a 3-account tick no longer
  flashes 3 PowerShell windows every 5 minutes. Same fix applied to `em_alert.py`'s relay call.

## [0.1.3] - 2026-07-06
### Fixed
- **register-task.ps1 heartbeat no longer dies after 24h.** The trigger used a fixed
  `RepetitionDuration (New-TimeSpan -Days 1)`, which silently stops the EmailMonitorTick heartbeat
  after one day, fatal for a monitor. Now uses a duration-less (indefinite) repetition so it runs
  every `IntervalMinutes` forever until removed. (Found while deploying the skill live.)

## [0.1.2] - 2026-07-06
### Security (privacy red line)
- **Redactor hardening.** The outbound alert/summary title redactor leaked non-numeric PII, an
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
  is installed, and **fall back to the Big Brother relay (send.py) when it is not**, fully
  pluggable, no behaviour change when the base is absent. Existing env/arg overrides still win.

## [0.1.0] - 2026-06-25
### Added
- Initial release. Thin orchestration skill: incremental IMAP watch, three-tier importance
  classifier, redacted Discord alerts, archive hook, schedule-reminder task pool integration,
  deadline normalizer, deterministic draft + AI-flavor linter, daily summary worker, EmailMonitorTick
  heartbeat template, and a 27-test program-judged acceptance suite.
