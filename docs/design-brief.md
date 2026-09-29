# Design Brief, email-monitor

> Design rationale for the public workflow. Current behavior and verification boundaries
> are documented in README.md and the workflow reference shards.

## Best references (match-or-beat)
- Google Priority Inbox / EACL 2026 industry track: importance = behavioral probability (will it be
  acted on), not topic; high-recall cheap gate -> LLM only for the uncertain few.
- IMAP RFC 3501 incremental sync best practice: UID + UIDVALIDITY watermark, never sequence numbers,
  never SEARCH SINCE; BODY.PEEK to avoid \Seen.
- FTC complaint-letter structure; dealer OTD negotiation playbook (3 asks + anchor + walk-away).

## Frontier ideas incorporated
- Two-layer model (global default + personal migration layer) as the user-feedback correction loop.
- due=signal / worker=content decoupling for digests (a scheduler trigger cannot carry a body).
- Deterministic draft linter as the regression signal (anti-self-deception).

## Anti-patterns avoided (20, see ARCHITECTURE §6)
- Second store / read base .db / SEARCH SINCE / UID-as-affair-key / per-mail item explosion /
  bare IDLE daemon / BODY[] writable select / All-Mail anchor / tick carrying summary body /
  naive +24h re-arm / PATH-resolved python in schtasks / repeated create_draft / SMTP in the loop /
  committing runtime DATA publicly / base DB on a sync drive / forwarding full bodies to Discord.
  Alert gists use bounded pattern redaction; names, dates, amounts and pure digit runs can remain.

## Original offline acceptance targets
- 27-test program-judged suite: classification >=0.85 + L0/L1 determinism; deadline tz 0 errors;
  base round-trip (idempotency/state machine/ext preserve/use-transition); draft compliance + AI-flavor
  0 hits; dedup (Message-ID + thread merge) 0 duplicates; IMAP watermark math.

## Scope & focus (one job, <=3 modules)
- Module 1 monitor+classify · Module 2 memory+alert · Module 3 draft+summary. One job: unattended
  inbox triage that drafts (never sends) and tracks affairs in the shared pool.
