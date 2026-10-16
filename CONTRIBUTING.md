# Contributing

This is a personal skill under the DaizeDong Skill Repo Spec v1.

Use the authorized source or approved host wheel described in the README for llmcall. The PyPI namesake is not this runtime.

Changes must keep:

1. Spec conformance: `python check_conformance.py .` (7 files, philosophy-first bilingual README,
   badge block, four-source-synced version, plugin fingerprint).
2. The description budget: keep the skill description short (`budget_check.py`).
3. The hard rules in `skills/email-monitor/SKILL.md`: never auto-send; pool only via the
   schedule-reminder CLI; no body/PII to Discord or git; UID+UIDVALIDITY incrementality.
4. The data boundary: `python "$(git config --get fleet.guards)/tools/data_boundary.py"` exits 0. Runs in pre-commit and pre-push.

If a gate blocks you, the gate is right. Never `--no-verify`.
