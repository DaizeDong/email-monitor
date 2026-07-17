# email-monitor, Design Philosophy

> One test governs every change: **does it reuse an existing substrate, or quietly build a second one?**
> And: **does any path send mail on the user's behalf?** If yes, it is wrong.

## P1, Reuse, never rebuild (own the seam, delegate the engines)

- **Symptom patch:** "I need a place to store affairs / schedule a daily job / push a phone alert," so
  write a new JSONL store, a new cron, a new bot.
- **Root cause:** three substrates already exist and are battle-tested on this machine: the Gmail IMAP
  toolchain (`gmail-imap-label.py` et al.), the schedule-reminder task pool (frozen CLI contract), and
  the Discord relay. A second store is a second source of truth that diverges.
- **Decision it produced:** email-monitor ships only the *new* seam (incremental watch + classify/draft
  orchestration + archive/summary hooks). The pool is reached strictly through `reminder.py <verb>
  --json`; the alert is the relay; the reads/labels are the existing tools.

## P2, A reply is never auto-sent

- **Symptom patch:** "drafting and sending are both email actions, wire them together for convenience."
- **Root cause:** an autonomous agent that can send mail in the user's name is a standing liability;
  one bad classification becomes an irreversible action. Drafting is reversible; sending is not.
- **Decision it produced:** the only output is a Gmail draft (`create_draft`). The SMTP send path
  (`send-gmail.ps1`) is physically isolated and never imported into this loop. The user clicks Send.

## P3, Privacy is a hard boundary, not a setting

- **Symptom patch:** "feed the email body to a web summarizer / push the subject to Discord / commit a
  record so it is backed up."
- **Root cause:** mail bodies are PII with a large blast radius. The public skill repo and any external
  API are the wrong custodians.
- **Decision it produced:** bodies are processed only by the local session model; Discord gets a
  one-line gist of the mail (owner-approved 2026-07-13) with every credential -- code, token,
  tracking number, URL, email address -- stripped, never the raw body; the public repo stores no PII; app passwords live in DPAPI, never in git,
  argv, or logs; the companion config repo is Mode B (secrets gitignored).

## P4, Programs judge, models do not self-grade

- **Symptom patch:** "the draft looks clean / the classification seems right."
- **Root cause:** self-assessment is how skills look done without being done.
- **Decision it produced:** every quality signal (draft compliance, AI-flavor, classification
  determinism, deadline tz, base round-trip, dedup, watermark math) is a regex/scan with a hard
  pass/fail in `tests/test_acceptance.py`, wired as the self-evolve regression gate.

## P5, Stability over latency (heartbeat, not a bare daemon)

- **Symptom patch:** "use IMAP IDLE for instant delivery."
- **Root cause:** a bare IDLE daemon silently stalls (a dead socket still `select()`s) and drops mail
  for days with no error.
- **Decision it produced:** the spine is a short heartbeat + UID/UIDVALIDITY reconciliation that
  backfills after sleep/shutdown; IDLE is an optional accelerant only, always backed by the heartbeat.
