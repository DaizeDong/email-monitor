# Contributing

This is a personal skill under the DaizeDong Skill Repo Spec v1.

Use the authorized source or approved host wheel described in the README for llmcall. CI's
dependency-install step also needs repository access; a failed checkout is an unmet prerequisite,
not a passing test run. The PyPI namesake is not this runtime.

Changes must keep:

1. The consumer suite: `python -m pytest skills/email-monitor/tests -q -m "not integration"`.
   Add `--reminder-source <schedule-reminder>/skills/schedule-reminder/scripts` to run the optional
   base round trips. Only its two implementation files are copied into a disposable test HOME;
   records and databases stay in temporary test storage, and mail/network effects remain blocked.
2. Spec conformance: `python check_conformance.py .` (7 files, philosophy-first bilingual README,
   badge block, four-source-synced version, plugin fingerprint).
3. The library token budget: a description change must keep the whole `~/.claude/skills` set under
   ~15k chars (`budget_check.py`).
4. The hard rules in `skills/email-monitor/SKILL.md`: never auto-send; pool only via the
   schedule-reminder CLI; no body/PII to Discord or git; UID+UIDVALIDITY incrementality.
5. The data boundary: `python guards/tools/data_boundary.py` exits 0. Runs in pre-commit, pre-push and CI.

Iteration is driven by `self-evolve` against the signals in `tests/test_acceptance.py`.

`--guards-source <kit-directory>` selects a frozen Guards candidate for the original read-only
tree/history scans. Those two scan commands use the invoking operator's policy profile; runtime
tests keep their synthetic HOME and blocked external effects. Other runtime imports still use the
bundled Guards dependency until its submodule is updated.

`python skills/email-monitor/tests/test_topic_regression.py` explicitly calls installed llmcall
with generated messages and a generated taxonomy. It checks every negative case and a positive
control. This live synthetic check does not read the operator's taxonomy or real mailbox, and it
does not establish production classification quality. The ordinary offline suite skips it.

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

## CI dependency access

The offline suite installs the private `llmcall` dependency declared in
`requirements.txt`. Set `LLMCALL_DEPLOY_KEY` to a dedicated SSH key registered
as a read-only deploy key on that dependency repository. Each consumer needs
its own key. The workflow loads it into a temporary SSH agent and trusts the
GitHub host keys returned by the HTTPS metadata API.

Fork pull requests do not receive this secret and cannot run the dependency
check. After reviewing a contribution, a maintainer must test the exact changes
on a trusted repository branch. A missing credential remains a failed check.
