# email-monitor, Design Philosophy

email-monitor coordinates existing mail, task and notification services. Its design keeps
mail sending under user control, makes model routing explicit and preserves recoverable state.

## P1, Reuse, never rebuild (own the seam, delegate the engines)

The Gmail IMAP toolchain (`gmail-imap-label.py` and related tools), optional schedule-reminder
task pool and Discord relay own their respective storage and delivery functions. A second task
store would create another source of truth that could diverge. email-monitor adds incremental
watch, classification and draft orchestration, plus archive and summary hooks. Pool access uses
`reminder.py <verb>` via subprocess with JSON responses; JSON output requires no `--json` flag.

## P2, A reply is never auto-sent

Classification errors must not send mail in the user's name. Drafting is reversible; sending
is not. Reply output is a Gmail draft (`create_draft`) for the user to review and send manually.
The SMTP send path (`send-gmail.ps1`) remains isolated and is never imported into this loop.

## P3, Keep private storage separate and model routing explicit

Real mail state belongs in a verified PRIVATE companion. A local orchestration process does
not establish that model calls stay on the machine: default agent classification includes body
content in prompts routed by installed `llmcall`, which may use external providers.
Enforced `runtime.local_only=true` requires heuristic classification and disabled topic models;
unverified agent/topic routes are rejected. Discord receives a redacted gist. App passwords stay
outside git, argv and logs; Mode B keeps secret files gitignored.

## P4, Programs judge, models do not self-grade

Deterministic checks assess draft compliance, AI-flavor, classification determinism, deadline
timezone handling, base round-trips, deduplication and watermark math. `tests/test_acceptance.py`
provides pass/fail signals for self-evolve regression checks. These checks establish their
tested contracts; they do not measure live classification quality or delivery readiness.

## P5, Stability over latency (heartbeat, not a bare daemon)

A bare IMAP IDLE daemon can stall on a dead socket without reporting an error. Short heartbeats
reconcile UID/UIDVALIDITY state and resume after sleep or shutdown, subject to first-use and
mailbox-generation baseline rules. IDLE is an optional latency improvement backed by that
heartbeat. See [monitor and classify](skills/email-monitor/reference/monitor-and-classify.md)
for cursor behavior and [delivery state](skills/email-monitor/reference/delivery-state.md)
for independent action checkpoints.
