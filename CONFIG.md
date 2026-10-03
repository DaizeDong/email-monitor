# email-monitor, Config

`email-monitor` is **config-bearing**: it reads per-user / per-machine state (account topology,
classification rules, draft templates, and DPAPI credential pointers) from a **separate, private
companion config repo** that you create and keep out of this public skill repo. Secrets never live
here. This file is the authoritative config contract (config-spec E1).

Operating mode: **Mode B**, the companion repo commits a zero-secret `registry.json`; real Gmail
app passwords are stored machine-bound in DPAPI at `~/.local/secrets/gmail-<slug>.cred`, and the repo
keeps only the `cred_path` pointer. Credentials under `secrets/*` are gitignored. Rules, templates
and runtime DATA belong under version control in the PRIVATE companion, never in the public tool.

## Discovery convention (how the skill finds your config), E2

The skill resolves its config **dir** in this order; the first that exists wins, then it reads
`<dir>/registry.json`:

1. `$EMAIL_MONITOR_CONFIG`, environment variable (recommended; location-independent).
2. `$EMAIL_MONITOR_CONFIG_DIR`, accepted alias.
3. `~/.email-monitor-config/`, dotfile-in-home fallback.
4. `~/.config/email-monitor-config/`, XDG-style fallback (Linux/macOS).

You may always override discovery with an explicit `--config <dir>/registry.json` on the runtime
scripts (`em_tick.py`, `em_summary.py`); the explicit path wins over the env order. If nothing
resolves, the heartbeat prints a structured configuration failure and exits nonzero without
sending an alert. Create or clone a verified PRIVATE companion before running `init_config.py`.

## Schema, `registry.json` (E1)

Committed, **zero secrets**. Fields:

```jsonc
{
  "schema_version": 1,                 // REQUIRED int — must be 1
  "spec": "email-monitor companion config (Mode B)", // OPTIONAL str — human note
  "mode": "B",                         // REQUIRED str — "B" (secrets gitignored + DPAPI)
  "machine": "<hostname>",             // OPTIONAL str — hostname placeholder (per-machine note)
  "accounts": [                        // REQUIRED array — at least one
    {
      "slug": "primary",               // REQUIRED str — kebab/snake id; keys cred + state
      "user": "you@example.com",       // REQUIRED str — the mailbox address
      "role": "primary",               // REQUIRED enum — primary | secondary | academic
      "imap_host": "imap.gmail.com",   // OPTIONAL str — default imap.gmail.com
      "cred_path": "~/.local/secrets/gmail-primary.cred", // OPTIONAL str — DPAPI pointer; MUST use ~ (portable, E5)
      "monitored_folders": ["INBOX"],  // OPTIONAL str[] — folders to watch
      "label_scheme": "EM/{priority}/{semantic}", // OPTIONAL str — Gmail label template
      "max_batch": 200,                // OPTIONAL int — max msgs per tick per account
      "health_last": "",               // OPTIONAL str — last healthy poll (runtime-stamped)
      "app_pw_rotated": ""             // OPTIONAL date — last app-password rotation
    }
  ],
  "discord": { "bot": "", "relay_fallback": true }, // OPTIONAL — alert channel + fallback
  "daily_summary": {                   // OPTIONAL obj — digest config
    "enabled": true,                   //   bool
    "cron_hook": "",                   //   str — external scheduler hook
    "local_time": "08:00",             //   str — local send time
    "tz": "America/New_York"           //   str — IANA tz for DST-correct re-arm
  },
  "topic_labeling": {                  // OPTIONAL obj — add-only topic labels, off by default
    "enabled": false                   //   bool — REQUIRED false default; an uninitialised
                                       //   machine must stay inert
  }
}
```

### Model policy and migration

Classification, topic labeling and quality review use installed `llmcall` in judge mode.
Routing, model, timeout and fallback are configured there. `classifier.mode="agent"`
retains its existing name for model-based text classification; it does not enable tool use.
`classifier.owner` supplies task context and `classifier.max_parallel` bounds concurrency.

Remove legacy `chain`, `providers`, `timeout`, `timeout_sec`, `model`, `reasoning_effort`,
`codex_model`, `codex_reasoning`, `claude_model` and `fallback` settings from classifier,
topic-labeling and quality-review blocks. The configuration doctor and heartbeat reject
these keys before operational setup, even if their feature is disabled.

The direct classification API accepts only omitted or `None` legacy `chain`, `providers`
and `timeout` arguments. Topic and review timeout arguments follow the same rule.
Classifier CLI switches `--chain`, `--timeout`, `--codex-model`, `--codex-reasoning` and
`--claude-model` report a migration error instead of silently choosing or ignoring a model.
Nonmodel subprocess deadlines remain bounded independently.

### Topic labeling, `rules/taxonomy.md` + `rules/sender_map.json` + `rules/labels.json`

These three files are what `topic_labeling.enabled: true` reads. Enabled topic judgments pass headers
and taxonomy to llmcall's current routing policy, which may use external providers; local-only mode
requires this capability disabled. They are DATA, not code: this
public skill repo ships the labeling method (evidence-gated, add-only, never de-inboxes); the
operator's own taxonomy, sender mappings, and label counts never appear here, only in the private
companion config. Nothing is written unless all three files are present and valid; a missing or
malformed set is treated as "not configured", not an error.

```
rules/
  taxonomy.md            # PRIVATE, versioned: prose standard describing what each label means,
                          #   fed verbatim to the model as the only standard it may reason from
  sender_map.json         # PRIVATE, versioned: deterministic pre-gate, checked before any model
                          #   call: {"by_address": {...}, "by_domain": {...}, "by_list_id": {...}},
                          #   each value a label name; address beats domain beats list identity
  labels.json              # PRIVATE, versioned: {"<account_slug>": ["<label>", ...]}, the closed
                          #   set of labels that account may be judged against; a model reply
                          #   naming anything outside this set is discarded, not coerced.
                          #   The pre-gate's own sender-map hits are checked against this same
                          #   set: a hit for a label this account never listed (a spelling drift
                          #   between accounts sharing one sender map) is dropped, not written.
                          #   OPTIONAL reserved key `_type_labels`: a list of labels that answer
                          #   "what KIND of mail is this" rather than "who sent it", so a
                          #   sender-map hit alone can never settle them and the model is always
                          #   asked. Falls back to the skill's own module default when absent;
                          #   if that default does not appear in an account's own list, the
                          #   type/source split is inert for that account and a tick logs a
                          #   warning naming it.
```

Every label the model proposes must carry a verbatim evidence span copied from the message's own
From or Subject; a label whose evidence does not occur in the input is dropped before it ever
reaches Gmail. A message with no clear evidence for any label gets no label -- omission over
commission, the same posture as the rest of this skill's classification.

Sender maps may contain SOURCE labels only. The doctor rejects TYPE labels in any of the
address, domain or list-ID buckets, including spelling that differs only in case. Runtime
judgment also drops such map hits, so model abstention or an outage cannot apply a TYPE label.

Filter exports report broader domain rules as uncompiled when they conflict with a narrower
address or domain mapping. Gmail applies every matching filter, so exporting both positives
would apply two labels even though the runtime chooses the more specific sender rule.

### Companion-repo layout

```
registry.json                 # committed, zero secrets (schema above)
rules/
  classification.yaml         # committed — global L0/L1 defaults
  project_vocab.yaml          # committed — controlled semantic vocabulary
  kill_list.txt               # committed — AI-flavor words the draft linter strips
  _personal_layer.json        # PRIVATE, versioned: VIP senders + personal overrides
  merged.json                 # PRIVATE, versioned: apply.py-derived (global + personal)
  taxonomy.md                 # PRIVATE, versioned: see "Topic labeling" above; init_config.py
  sender_map.json              # PRIVATE, versioned: stamps a synthetic skeleton for all three
  labels.json                   # PRIVATE, versioned: so `topic_labeling.enabled: true` has
                                 #   somewhere real to read once you fill them in
templates/
  business.txt dealer.txt support.txt personal.txt   # committed draft profiles
secrets/
  _accounts.env.template      # committed template (placeholders only)
  README.md                   # committed — declares Mode B
  *.env / *.cred              # GITIGNORED — real values never enter git
data/
  state/                     # PRIVATE, versioned: cursors, scoped observations, action and summary ledgers
  email-monitor.log          # PRIVATE, versioned: runtime diagnostics
  pool.db                    # PRIVATE, versioned: optional reminder pool
```

## Secrets, Mode B (E6)

The companion config repo is **separate and private**. `secrets/*` is **gitignored**, real values
never enter git. Real Gmail app passwords live in DPAPI (`~/.local/secrets/gmail-<slug>.cred`), which is
machine-bound and does not travel: re-capture per machine. This public skill repo additionally
ignores `registry.json`, `*.cred`, `rules/merged.json` etc. defensively so a local test config never
leaks. Neither repo ever echoes a secret.

## First-time setup (E3)

```bash
# 1. Stamp a conformant, zero-secret companion skeleton (deterministic — E4):
python scripts/init_config.py        # -> ~/.email-monitor-config/  (or --out <dir>)

# 2. Point the skill at it (skip if you used the default path):
export EMAIL_MONITOR_CONFIG=~/.email-monitor-config

# 3. Edit registry.json (real accounts), capture app passwords into DPAPI (Mode B),
#    copy rules/_personal_layer.json.template -> _personal_layer.json, then confirm:
python scripts/verify_config.py      # doctor: PASS/FAIL per check, names gaps
```

## Switching between two configs (hot-swap), E5

Storage is relative to the selected PRIVATE companion. Interpreter, credential-reference and
helper paths may be absolute resources outside Git. Keep separate PRIVATE companions and switch
the configuration environment variable when needed:

```bash
export EMAIL_MONITOR_CONFIG=~/configs/work        # config A
export EMAIL_MONITOR_CONFIG=~/configs/personal    # config B — same skill, different state
```

Run `verify_config.py --config-dir <companion> --json` for each configuration. A newly stamped
skeleton is not ready: select a working interpreter with llmcall installed, populate the account
and rules, and establish a PRIVATE Git companion before the doctor can report ready.

## Runtime and delivery state

Schema version remains 1. `runtime.python` selects the actual interpreter; the doctor runs a
bounded Python/llmcall import probe. Missing runtime settings use the invoking interpreter for
compatibility. `register-task.ps1` consumes the same selection and requires a ready doctor result
before registration. Absolute interpreter, credential-reference and helper resource paths are
valid; output paths still require PRIVATE Git proof.

`storage.state_dir`, `storage.log` and `storage.db` default to `data/state`,
`data/email-monitor.log` and `data/pool.db` within the selected PRIVATE companion. These are
versioned runtime DATA. CLI `--state-dir` and `--db` overrides receive the same checks.
The pinned Guards companion API establishes the nearest worktree, including linked worktrees,
and checks every physical and effective fetch/push destination and configured remote selector.
All destinations need a local PRIVATE visibility receipt refreshed within the last 30 days.
Missing, malformed, stale, future or nonprivate receipts fail closed. The doctor reports the
proven repository names and does not refresh receipts or create DATA. Refresh the visibility
receipt separately when proof is unavailable.

The companion must have a committed HEAD, and runtime outputs must remain eligible for its
version history. Source-tree, nested-repository, ignored and unversioned outputs fail before
mail access. Filesystem aliases and hardlinked output files are rejected before resolution.
State, report and filter exports use exclusive temporary files and repeat the PRIVATE proof
before writing and publishing. Git configuration changes during publication abort the write.

SSH aliases and HTTPS routing/trust follow the shared Guards policy, read locally without
executing SSH or network commands. Every plausible SSH configuration chain must establish
the same GitHub destination; ambiguous routing, proxy commands and weakened trust fail closed.
There is no public-tree fallback.

`runtime.local_only=true` permits heuristic classification with topic models disabled. A provider
name or chain label does not establish locality. The current llmcall interface offers no
enforceable local-transport proof, so agent and topic model modes fail before credentials, mail
or model calls under that policy. The bundled summary is deterministic and uses no model. A
custom summary worker cannot be verified under local_only.

`draft.signature` is required and must be a nonempty single line; `draft.language` is `en`, `zh` or `any`.
`draft.style` supports positive `max_lines` and `max_sentences` integers plus boolean
`allow_markdown`. Malformed constraints fail visibly. Run the draft linter with the registry
via `--config`; existing `--profile` and `--json` flags remain available. There is no implicit
signature: the CLI reports invalid configuration when `--config` or `draft.signature` is missing,
and the Python `lint(..., config=...)` API raises `ValueError` for missing or invalid settings.
This intentionally changes callers that depended on a built-in identity. Valid configurations
retain the same violation list and CLI JSON fields.

The initializer stamps `Your Name` as a placeholder and renders new templates from the selected
registry's signature. Set the intended `draft.signature` and keep template signatures in sync.
Re-running without `--force` preserves existing registry and template bytes and fills only missing
files; malformed or missing draft settings refuse before template creation. `--force` resets the
skeleton, including custom registry and template content, so use it only for an intentional reset.
Existing companions also retain their `.gitignore` on a normal rerun. Review any older DATA ignore
rules and version those records in the PRIVATE companion; credential exclusions must remain.

Read [delivery-state.md](skills/email-monitor/reference/delivery-state.md) before recovery or
rollout. Doctor readiness covers configuration and runtime probes. Controlled-account delivery,
external adapter receipts, scheduled-task XML and heartbeat require separate live measurements.
