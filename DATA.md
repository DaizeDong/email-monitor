# Private email-monitor data

[storage.contract.json](storage.contract.json) declares default companion paths and
retention. [CONFIG.md](CONFIG.md) and the existing
[delivery-state protocol](skills/email-monitor/reference/delivery-state.md) remain
authoritative for configuration, action identity and recovery.

Keep the current registry, effective classification rules, the layers needed to
rebuild them, topic taxonomy/maps/label sets and approved draft profiles.
Credentials are resource references; their actual machine-bound material follows
the separately configured private backup and reauthorization procedure.

Default runtime paths are `data/state`, `data/email-monitor.log` and `data/pool.db`.
Account and summary state record observation and delivery separately. Preserve
pending or uncertain actions, matching receipts, topic retries and identities needed
to prevent replay. A newer cursor, a zero process exit or a completed action elsewhere
does not establish this action's delivery. Restore state before enabling a writer.
The optional reminder database follows its own schema and SQLite recovery rules.

Logs and compiled filter exports can be rebuilt after their specific review or
diagnosis ends. An independent mailbox baseline needed for rollback is a separate
dependency, and cannot be discarded merely because a new compiled XML exists.
Quality findings are retained only for an unresolved review or selected deliverable.
Use `data/filter-exports/<name>.xml` and `data/reviews/<name>.json` for these explicit
CLI output selections. The writer enforces PRIVATE storage; these paths are storage
conventions, not automatic scheduled exports.

Overrides and legacy paths need their own explicit contract entries. Old reports,
registry backup copies, filter baselines, development histories and companion helper
scripts are not automatically core just because they are private or committed.
Inventory reports them for individual dependency review; this contract does not
authorize their deletion or claim that they have been migrated.

Use skill-smith's shared `storage_contract.py` for schema validation and metadata
inventory. It does not execute domain schemas, fetch mail, call models, send alerts,
register tasks or prove recovery. Any cleanup must first establish that the relevant
writer has stopped and that no action or database transaction is unresolved.
Local retention changes do not erase Git history.
