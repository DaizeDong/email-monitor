# Contributing

This is a personal skill under the DaizeDong Skill Repo Spec v1. Changes must keep:

1. The acceptance suite green: `cd skills/email-monitor && pytest -q` (27 program-judged signals).
2. Spec conformance: `python check_conformance.py .` (7 files, philosophy-first bilingual README,
   badge block, four-source-synced version, plugin fingerprint).
3. The library token budget: a description change must keep the whole `~/.claude/skills` set under
   ~15k chars (`budget_check.py`).
4. The hard rules in `skills/email-monitor/SKILL.md`: never auto-send; pool only via the
   schedule-reminder CLI; no body/PII to Discord or git; UID+UIDVALIDITY incrementality.

Iteration is driven by `self-evolve` against the signals in `tests/test_acceptance.py`.
