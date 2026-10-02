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

Every case must be independently invented. The fixture must be byte-identical to
`make_fixtures.py` output, which `data_boundary.py` checks at commit time. This detects
manual edits to generated output; review the case table as well to ensure its inputs
contain no operational records. Name the behavior each case tests and use only synthetic
addresses and content.

If a gate blocks you, the gate is right. Never `--no-verify`.
