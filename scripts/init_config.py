#!/usr/bin/env python3
"""Stamp a spec-conformant companion config repo for email-monitor (config-spec E3/E4).

email-monitor is config-bearing (Mode B): the real account topology, rules, draft templates and
DPAPI credential pointers live in a SEPARATE, private companion repo -- never in this public skill
repo. This script stamps an empty, conformant skeleton of that companion repo. It is
template-driven and deterministic: re-running with the same --out produces byte-identical files
(E4). It NEVER writes a real secret and NEVER echoes one.

Discovery convention the skill uses (also CONFIG.md, E2). The config dir resolves, first hit wins:
  1. $EMAIL_MONITOR_CONFIG          (recommended; location-independent)
  2. $EMAIL_MONITOR_CONFIG_DIR      (accepted alias)
  3. ~/.email-monitor-config/       (dotfile-in-home fallback)
  4. ~/.config/email-monitor-config/ (XDG-style fallback)
The skill then reads <dir>/registry.json.

Usage:
  python init_config.py [--out <dir>] [--force]
--out   target dir; default = the discovery dotfile path ~/.email-monitor-config/.
Stdlib only. Cross-platform.
"""
import argparse
import json
import os
import sys

ENV_VAR = "EMAIL_MONITOR_CONFIG"
DEFAULT_DIR = "~/.email-monitor-config"

# registry.json -- committed, ZERO secrets. Placeholders only; cred_path uses ~ so the dir is
# self-contained / portable (E5: no machine-bound absolute path). "machine" is a literal
# placeholder to keep init deterministic across machines (E4).
REGISTRY = {
    "schema_version": 1,
    "spec": "email-monitor companion config (Mode B; secrets gitignored, real creds in DPAPI)",
    "mode": "B",
    "machine": "<hostname>",
    "accounts": [
        {
            "slug": "primary",
            "user": "you@example.com",
            "role": "primary",
            "imap_host": "imap.gmail.com",
            "cred_path": "~/.local/secrets/gmail-primary.cred",
            "monitored_folders": ["INBOX"],
            "label_scheme": "EM/{priority}/{semantic}",
            "max_batch": 200,
            "health_last": "",
            "app_pw_rotated": "",
        }
    ],
    "discord": {"bot": "", "relay_fallback": True},
    "daily_summary": {
        "enabled": True,
        "cron_hook": "",
        "local_time": "08:00",
        "tz": "America/New_York",
    },
}

GITIGNORE = """\
# email-monitor companion config -- secrets gate (config-spec E6 / Mode B).
# Real values never enter git. Back them up out-of-band; real app passwords live in DPAPI
# (~/.local/secrets/gmail-<slug>.cred), this repo keeps only pointers.
secrets/*
!secrets/README.md
!secrets/.gitkeep
!secrets/_accounts.env.template
*.env
!*.env.template
*.cred
*.creds
*.key
*.pem
.credentials.json
# derived / personal layers (never committed)
rules/merged.json
rules/_personal_layer.json
state/*
!state/SCHEMA.md
!state/.gitkeep
"""

SECRETS_README = """\
# secrets/ -- Mode B (gitignored)

Real secret values live here and are **gitignored** (see ../.gitignore); they never enter git.
email-monitor uses **Mode B**: the real Gmail app passwords are stored machine-bound in DPAPI at
`~/.local/secrets/gmail-<slug>.cred`, and this repo keeps only the `cred_path` pointer in registry.json.

- Copy `_accounts.env.template` -> `_accounts.env` (gitignored) only if you keep env-style creds.
- Per the config repo's own `scripts/capture-app-pw.ps1` / `resolve-cred.ps1`, capture each app
  password into DPAPI on THIS machine (DPAPI ciphertext does not travel; re-capture per machine).
- Back up out-of-band (encrypted drive / cloud sync). Files MUST be UTF-8 without BOM.
"""

ACCOUNTS_ENV_TEMPLATE = """\
# _accounts.env.template -- copy to _accounts.env (gitignored) ONLY if you keep env-style creds.
# Preferred path is DPAPI (Mode B): leave this empty and use cred_path in registry.json.
# One line per account slug; UPPER_SNAKE placeholders, UTF-8 without BOM.
EM_PRIMARY_APP_PW=<gmail-app-password-or-leave-blank-and-use-DPAPI>
"""

CLASSIFICATION_YAML = """\
# classification.yaml -- global L0/L1 classification defaults (committed, no PII).
# Personal overrides go in _personal_layer.json (gitignored); apply.py merges -> merged.json.
priorities: [URGENT, ACTION, FYI, NOISE]
l0_rules:
  urgent_from: []          # exact senders that are always URGENT
  noise_from: []           # senders auto-archived as NOISE
  action_subject: []       # subject substrings implying an action is required
l1:
  weights: {sender: 0.4, subject: 0.4, recency: 0.2}
  urgent_threshold: 0.75
  action_threshold: 0.50
"""

PROJECT_VOCAB_YAML = """\
# project_vocab.yaml -- controlled vocabulary so semantic labels stay stable (committed, no PII).
semantic_labels: [payment, scheduling, account, shipping, legal, personal, newsletter, receipt]
aliases:
  invoice: payment
  meeting: scheduling
"""

KILL_LIST = """\
# kill_list.txt -- AI-flavor words/phrases the draft linter strips (one per line; committed).
delve
tapestry
moreover
in conclusion
it is important to note
I hope this email finds you well
"""

PERSONAL_LAYER_TEMPLATE = """\
{
  "_comment": "Copy to _personal_layer.json (gitignored). Holds VIP senders (PII) + personal overrides.",
  "vip_from": ["<vip@example.com>"],
  "l0_rules": {"urgent_from": [], "noise_from": []}
}
"""

TEMPLATES = {
    "business.txt": "Hi {name},\n\n{body}\n\nBest,\nDaize Dong\n",
    "dealer.txt": "Hi {name},\n\n{body}\n\nThanks,\nDaize Dong\n",
    "support.txt": "Hello,\n\n{body}\n\nRegards,\nDaize Dong\n",
    "personal.txt": "Hi {name},\n\n{body}\n\nDaize Dong\n",
}

STATE_SCHEMA = """\
# state/ -- runtime cursors & seen-set (ALL gitignored except this file).
# Per account: last_uid + UIDVALIDITY watermark, and an X-GM-MSGID seen-set for dedupe.
# These are machine/runtime state, never committed.
"""


def env_var():
    return ENV_VAR


def default_dir():
    return os.path.expanduser(DEFAULT_DIR)


def write(path, content, force):
    if os.path.exists(path) and not force:
        print("  SKIP (exists): %s" % path)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    print("  wrote: %s" % path)


def main():
    ap = argparse.ArgumentParser(description="Stamp the email-monitor companion config (Mode B).")
    ap.add_argument("--out", default=None, help="target dir; default ~/.email-monitor-config/")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    out = a.out or default_dir()
    out = os.path.abspath(os.path.expanduser(out))
    print("Init email-monitor companion config (Mode B) at %s" % out)
    print("Discovery env var: %s  (fallback %s)" % (env_var(), default_dir()))

    write(os.path.join(out, "registry.json"),
          json.dumps(REGISTRY, indent=2, ensure_ascii=False) + "\n", a.force)
    write(os.path.join(out, ".gitignore"), GITIGNORE, a.force)
    write(os.path.join(out, "rules", "classification.yaml"), CLASSIFICATION_YAML, a.force)
    write(os.path.join(out, "rules", "project_vocab.yaml"), PROJECT_VOCAB_YAML, a.force)
    write(os.path.join(out, "rules", "kill_list.txt"), KILL_LIST, a.force)
    write(os.path.join(out, "rules", "_personal_layer.json.template"), PERSONAL_LAYER_TEMPLATE, a.force)
    for name, body in TEMPLATES.items():
        write(os.path.join(out, "templates", name), body, a.force)
    write(os.path.join(out, "secrets", "README.md"), SECRETS_README, a.force)
    write(os.path.join(out, "secrets", "_accounts.env.template"), ACCOUNTS_ENV_TEMPLATE, a.force)
    write(os.path.join(out, "secrets", ".gitkeep"), "", a.force)
    write(os.path.join(out, "state", "SCHEMA.md"), STATE_SCHEMA, a.force)
    write(os.path.join(out, "state", ".gitkeep"), "", a.force)

    print("\nNext:")
    print("  1) Edit registry.json: set real account slug/user/role + cred_path per account.")
    print("  2) Capture each app password into DPAPI (config repo's capture-app-pw.ps1), Mode B.")
    print("  3) Copy rules/_personal_layer.json.template -> _personal_layer.json (gitignored), fill VIPs.")
    print("  4) export %s=%s   (or use the default path)" % (env_var(), out))
    print("  5) python scripts/verify_config.py   # doctor: confirms the config is ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
