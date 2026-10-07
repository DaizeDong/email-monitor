# Private email-monitor data

[storage.contract.json](storage.contract.json) declares paths, producers, consumers,
retention and restoration relative to the exact initialized PRIVATE companion Git
root. [CONFIG.md](CONFIG.md) and the existing
[delivery-state protocol](skills/email-monitor/reference/delivery-state.md) remain
authoritative for configuration, action identity and recovery. Contract entries do
not initialize state, run mailbox operations or authorize removal.

Keep the current registry, effective classification rules, required rule layers,
topic taxonomy/maps/label sets and approved draft profiles. The configured credential
capture/resolution helpers, classification-review helper and four operator runbooks
are necessary while their consumers or recovery procedures reference them. Verify
outdated runbook paths before use. Initializer rule defaults can be regenerated;
privately edited inputs must be reconciled before replacing them. Credential material
follows the configured private backup and reauthorization procedure.

Default runtime paths are `data/state`, `data/email-monitor.log` and the optional
`data/pool.db`. The registry may select the existing Schedule database instead;
this contract does not create a second reminder store. Preserve pending or uncertain
actions, matching receipts, topic retries and identities needed to prevent replay.
A newer cursor, a zero process exit or completion elsewhere does not establish this
action's delivery. Restore state before enabling a writer. SQLite sidecars remain
subject to the database owner's consistent backup and recovery procedure.

The migrated classification report is `data/reports/classification-review.txt`, and
quality findings use `data/quality-review/<name>.json`. Explicit CLI conventions
`data/filter-exports/<name>.xml` and `data/reviews/<name>.json` remain declared for
selected outputs. These conventions do not schedule a review or an export. Keep
quality findings while corrections or a selected deliverable depend on the original
observation; a later sample is different evidence. Logs and derived reports may be
rotated after their diagnosis and evidence dependencies end, with writer coordination.

Narrow `filters-baseline/` entries distinguish independent native exports and JSON
snapshots from compiled candidates and probes. Keep independent observations selected
for rollback, unresolved corrections or final comparison, together with their
provenance and pending-review notes. A new compiled XML cannot recreate an older
mailbox observation. Baseline filenames and dates do not prove current mailbox state;
filter import still requires its separately authorized review.

`data/gmail-triage/2026-08-19/` is a historical triage development campaign, and
`data/legacy-self-evolve/` preserves historical events/state/target metadata. Their
`retired` class prohibits new campaign writes and imposes a conditional hold: extract
useful unique code, required DATA, selected results and correction/rollback evidence;
then reconcile consumers, unresolved outcomes and recovery dependencies and prove
writer inactivity. The self-evolve index is not a resume manifest. Classification
alone establishes neither closure nor permission to delete.

Undeclared newer triage/export groups, registry backup copies, nonselected baseline
candidates/probes, private review/design notes and unreferenced helper experiments
remain inventory gaps. Private storage and Git history do not make them core. They
need producer, selected-output and recovery review before a precise declaration;
no broad rule hides them. The aggregate review threshold is 64 MiB, excluding Git administration. A breached
threshold remains a failed capacity check; required DATA must be reconciled without
dropping it to make the check pass. Declared budgets never authorize removal.

Run skill-smith's shared checker from its canonical source checkout:

```text
python skills/skill-smith/scripts/storage_contract.py validate --repo <source-checkout>
python skills/skill-smith/scripts/storage_contract.py check --repo <source-checkout> --companion <private-repository-root> --json
```

Pass the exact companion Git root, including its configuration and recovery files;
passing only its `data/` child omits part of the contract and is not a valid check.
The shared checker proves PRIVATE admission and inspects metadata, ownership and
sizes. Domain validators inspect record contents. An inventory gap or unobserved
requirement is a failed check, even when schema validation passes.
