# Contributing

This is a personal skill under the DaizeDong Skill Repo Spec v1. Changes must keep:

1. The acceptance suite green: `cd skills/email-monitor && pytest -q` (27 program-judged signals).
2. Spec conformance: `python check_conformance.py .` (7 files, philosophy-first bilingual README,
   badge block, four-source-synced version, plugin fingerprint).
3. The library token budget: a description change must keep the whole `~/.claude/skills` set under
   ~15k chars (`budget_check.py`).
4. The hard rules in `skills/email-monitor/SKILL.md`: never auto-send; pool only via the
   schedule-reminder CLI; no body/PII to Discord or git; UID+UIDVALIDITY incrementality.
5. The data boundary: `python tools/data_boundary.py` exits 0. Runs in pre-commit, pre-push and CI.

Iteration is driven by `self-evolve` against the signals in `tests/test_acceptance.py`.

## Never hand-edit a test fixture

`skills/email-monitor/tests/golden_classify.jsonl` is **generated output**. Do not open it.

```
edit the CASE TABLE in tools/make_fixtures.py  ->  python tools/make_fixtures.py  ->  commit both
```

The reason is not tidiness. This skill reads a real inbox, and the 2026-07 audit found that the
golden file had been built by pasting real emails out of it — real senders, a real person, a real
employer. That is not carelessness, it is the path of least resistance: anyone writing a classifier
test needs a realistic message, and a real one is always within reach.

So the fixture is required to be byte-identical to what `make_fixtures.py` emits, and
`data_boundary.py` checks it at commit time. **A real email cannot be regenerated.** Paste one in and
the gate fails immediately — even if it looks completely innocuous, which is precisely what a content
scanner cannot promise. Adding a case forces you to state which classifier path you are pinning and
to invent the message rather than borrow it.

If a gate blocks you, the gate is right. Never `--no-verify`.
