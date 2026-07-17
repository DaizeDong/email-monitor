# email-monitor, Config

`email-monitor` is **config-bearing**: it reads per-user / per-machine state (account topology,
classification rules, draft templates, and DPAPI credential pointers) from a **separate, private
companion config repo** that you create and keep out of this public skill repo. Secrets never live
here. This file is the authoritative config contract (config-spec E1).

Operating mode: **Mode B**, the companion repo commits a zero-secret `registry.json`; real Gmail
app passwords are stored machine-bound in DPAPI at `~/.local/secrets/gmail-<slug>.cred`, and the repo
keeps only the `cred_path` pointer. `secrets/*`, `rules/merged.json`, `rules/_personal_layer.json`
and `state/*` are gitignored.

## Discovery convention (how the skill finds your config), E2

The skill resolves its config **dir** in this order; the first that exists wins, then it reads
`<dir>/registry.json`:

1. `$EMAIL_MONITOR_CONFIG`, environment variable (recommended; location-independent).
2. `$EMAIL_MONITOR_CONFIG_DIR`, accepted alias.
3. `~/.email-monitor-config/`, dotfile-in-home fallback.
4. `~/.config/email-monitor-config/`, XDG-style fallback (Linux/macOS).

You may always override discovery with an explicit `--config <dir>/registry.json` on the runtime
scripts (`em_tick.py`, `em_summary.py`); the explicit path wins over the env order. If nothing
resolves, the skill does not crash, it prints how to set `$EMAIL_MONITOR_CONFIG` / run
`init_config.py` and exits cleanly (graceful degradation).

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
  }
}
```

### Companion-repo layout

```
registry.json                 # committed, zero secrets (schema above)
rules/
  classification.yaml         # committed — global L0/L1 defaults
  project_vocab.yaml          # committed — controlled semantic vocabulary
  kill_list.txt               # committed — AI-flavor words the draft linter strips
  _personal_layer.json        # GITIGNORED — VIP senders (PII) + personal overrides
  merged.json                 # GITIGNORED — apply.py-derived (global + personal)
templates/
  business.txt dealer.txt support.txt personal.txt   # committed draft profiles
secrets/
  _accounts.env.template      # committed template (placeholders only)
  README.md                   # committed — declares Mode B
  *.env / *.cred              # GITIGNORED — real values never enter git
state/
  SCHEMA.md                   # committed — describes cursors/seen-set
  *                           # GITIGNORED — UID/UIDVALIDITY watermark + X-GM-MSGID seen-set
```

## Secrets, Mode B (E6)

The companion config repo is **separate and private**. `secrets/*` is **gitignored**, real values
never enter git. Real Gmail app passwords live in DPAPI (`~/.local/secrets/gmail-<slug>.cred`), which is
machine-bound and does not travel: re-capture per machine. This public skill repo additionally
ignores `registry.json`, `*.cred`, `rules/merged.json` etc. defensively so a local test config never
leaks. Neither repo ever echoes a secret.

## First-time setup (E3), succeeds on the first try

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

A config dir is **self-contained**, `cred_path` uses `~`, no hardcoded absolute paths. Keep as many
as you like and switch by repointing the env var; nothing else changes:

```bash
export EMAIL_MONITOR_CONFIG=~/configs/work        # config A
export EMAIL_MONITOR_CONFIG=~/configs/personal    # config B — same skill, different state
```

Verify the swap: `init_config.py --out ~/configs/work` and `--out ~/configs/personal`, run
`verify_config.py` against each, then flip `$EMAIL_MONITOR_CONFIG` between them, both must report
READY.
