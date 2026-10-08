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
  3. Existing EMAIL_MONITOR_DATA_DIR (data child selects parent), then sibling email-monitor-config,
     ~/.email-monitor-config and ~/.email-monitor-data through pinned Guards.
The skill then reads <dir>/registry.json.

Usage:
  python init_config.py [--out <dir>] [--force]
--out   target dir; default = the selected companion or a sibling email-monitor-config repository.
Stdlib only. Cross-platform.
"""
import argparse
from functools import partial
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "email-monitor" / "scripts"))
from em_lint_rules import draft_config
import em_runtime

ENV_VAR = "EMAIL_MONITOR_CONFIG"
DEFAULT_DIR = str(Path(__file__).resolve().parents[1].with_name('email-monitor-config'))

# registry.json -- committed, ZERO secrets. Placeholders only; cred_path uses ~ so the dir is
# self-contained / portable (E5: no machine-bound absolute path). "machine" is a literal
# placeholder to keep init deterministic across machines (E4).
REGISTRY = {
    "schema_version": 1,
    "spec": "email-monitor companion config (Mode B; secrets gitignored, real creds in DPAPI)",
    "mode": "B",
    "machine": "<hostname>",
    "runtime": {"python": "<absolute-path-to-python>", "local_only": True},
    "classifier": {"mode": "heuristic"},
    "storage": {"state_dir": "data/state", "log": "data/email-monitor.log", "db": "data/pool.db"},
    "draft": {"signature": "Your Name", "language": "en",
              "style": {"max_lines": 8, "max_sentences": 5, "allow_markdown": False}},
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
    # off by default (CONFIG.md): an uninitialised machine must stay inert until the
    # operator has actually populated rules/taxonomy.md + sender_map.json + labels.json.
    "topic_labeling": {"enabled": False},
}

GITIGNORE = """\
# email-monitor companion config -- secrets gate (config-spec E6 / Mode B).
# Credentials never enter git. Back them up out-of-band; real app passwords live in DPAPI
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
# Rules, templates and runtime DATA are versioned in this PRIVATE companion.
# They must never be copied into the public skill repository.
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
# Personal overrides go in _personal_layer.json; apply.py merges -> merged.json.
# Version both files in the PRIVATE companion, never in the public skill repository.
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
  "_comment": "Copy to _personal_layer.json in this PRIVATE companion and version it there. Holds VIP senders and personal overrides.",
  "vip_from": ["<vip@example.com>"],
  "l0_rules": {"urgent_from": [], "noise_from": []}
}
"""

# The three files topic_labeling.enabled: true reads (CONFIG.md, "Topic labeling"). All three
# are versioned in the PRIVATE companion: this is a skeleton to fill in. Content is
# synthetic (example.com) on purpose -- the operator's real labels, senders and mailing lists
# are private data and never belong in this public skill repo, only in the private companion.
TAXONOMY_MD = """\
# taxonomy.md -- the topic labeling standard (versioned in the PRIVATE companion).
#
# This is prose, fed VERBATIM to the model as the only standard it may reason from (CONFIG.md).
# Write one short paragraph per label: what it means, and what it explicitly does NOT mean, so
# the model has something falsifiable to check a candidate label against instead of guessing.
# Every proposed label must also carry a verbatim evidence span from the message's own From or
# Subject; a label with no such span is dropped before it reaches Gmail, so keep labels tied to
# things that actually show up there rather than to body content the model never sees.
#
# Example (synthetic, replace with your own):
#
# Payments
#   A message says money already moved: a receipt, an invoice, a statement. Not a marketing
#   email that merely mentions a price. Example sender: billing@example.com.
#
# Scheduling
#   A message proposes, confirms, or changes a specific date/time commitment. Not a generic
#   newsletter that happens to list events. Example sender: calendar@example.org.
"""

SENDER_MAP_JSON = json.dumps(
    {"version": 1, "by_address": {}, "by_domain": {}, "by_list_id": {}},
    indent=2, ensure_ascii=False,
) + "\n"

# Empty per-account mapping (versioned in the PRIVATE companion): {"<slug>": ["<label>", ...]}.
# An account with no entry here is simply never asked to topic-label -- add-only, off by default,
# same posture as topic_labeling.enabled in registry.json.
LABELS_JSON = json.dumps({}, indent=2, ensure_ascii=False) + "\n"

TEMPLATES = {
    "business.txt": "Hi {name},\n\n{body}\n\nBest,\n{signature}\n",
    "dealer.txt": "Hi {name},\n\n{body}\n\nThanks,\n{signature}\n",
    "support.txt": "Hello,\n\n{body}\n\nRegards,\n{signature}\n",
    "personal.txt": "Hi {name},\n\n{body}\n\n{signature}\n",
}

STATE_SCHEMA = """\
# state/ -- runtime cursors & seen-set (versioned in the PRIVATE companion).
# Per account: last_uid + UIDVALIDITY watermark, and an X-GM-MSGID seen-set for dedupe.
# These are private runtime DATA; keep their history in the companion repository.
"""


def env_var():
    return ENV_VAR


def default_dir():
    return str(em_runtime.companion_dir() or Path(DEFAULT_DIR))


def write(path, content, force, *, root=None):
    if os.path.exists(path) and not force:
        print("  SKIP (exists): %s" % path)
        return
    if root is None:
        raise ValueError("Configuration writes require an explicit PRIVATE companion root")
    em_runtime.authorize_config_write(root, path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    em_runtime.authorize_config_write(root, path)
    # Check the opened file before truncation so hardlink aliases keep their bytes.
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o666)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as f:
        if os.fstat(f.fileno()).st_nlink > 1:
            raise ValueError("Refusing to overwrite a hardlinked configuration file: %s" % path)
        f.truncate(0)
        f.write(content)
    print("  wrote: %s" % path)


def main():
    ap = argparse.ArgumentParser(description="Stamp the email-monitor companion config (Mode B).")
    ap.add_argument("--out", default=None, help="target dir; default selected or sibling PRIVATE companion")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    out = a.out or default_dir()
    out = os.path.abspath(os.path.expanduser(out))
    write_config = partial(write, root=out)
    registry_path = os.path.join(out, "registry.json")
    try:
        registry = REGISTRY
        if os.path.exists(registry_path) and not a.force:
            with open(registry_path, encoding="utf-8-sig") as handle:
                registry = json.load(handle)
        if not isinstance(registry, dict) or "draft" not in registry:
            raise ValueError("registry must contain a draft configuration")
        signature = draft_config(registry["draft"])["signature"]
    except (OSError, ValueError) as error:
        print("Cannot initialize draft templates: invalid configuration: " + str(error))
        return 1
    try:
        em_runtime.authorize_config_write(out, registry_path)
    except (OSError, ValueError, RuntimeError) as error:
        print("Cannot initialize PRIVATE configuration: " + str(error))
        return 1
    print("Init email-monitor companion config (Mode B) at %s" % out)
    print("Discovery env var: %s  (fallback %s)" % (env_var(), default_dir()))

    try:
        write_config(registry_path, json.dumps(REGISTRY, indent=2, ensure_ascii=False) + "\n", a.force)
    except (OSError, ValueError) as error:
        print("Cannot initialize registry: " + str(error))
        return 1
    write_config(os.path.join(out, ".gitignore"), GITIGNORE, a.force)
    write_config(os.path.join(out, "rules", "classification.yaml"), CLASSIFICATION_YAML, a.force)
    write_config(os.path.join(out, "rules", "project_vocab.yaml"), PROJECT_VOCAB_YAML, a.force)
    write_config(os.path.join(out, "rules", "kill_list.txt"), KILL_LIST, a.force)
    write_config(os.path.join(out, "rules", "_personal_layer.json.template"), PERSONAL_LAYER_TEMPLATE, a.force)
    write_config(os.path.join(out, "rules", "taxonomy.md"), TAXONOMY_MD, a.force)
    write_config(os.path.join(out, "rules", "sender_map.json"), SENDER_MAP_JSON, a.force)
    write_config(os.path.join(out, "rules", "labels.json"), LABELS_JSON, a.force)
    # Keep configured braces literal when the remaining name/body fields are formatted.
    template_signature = signature.replace("{", "{{").replace("}", "}}")
    for name, body in TEMPLATES.items():
        write_config(os.path.join(out, "templates", name), body.replace("{signature}", template_signature), a.force)
    write_config(os.path.join(out, "secrets", "README.md"), SECRETS_README, a.force)
    write_config(os.path.join(out, "secrets", "_accounts.env.template"), ACCOUNTS_ENV_TEMPLATE, a.force)
    write_config(os.path.join(out, "secrets", ".gitkeep"), "", a.force)
    write_config(os.path.join(out, "state", "SCHEMA.md"), STATE_SCHEMA, a.force)
    write_config(os.path.join(out, "state", ".gitkeep"), "", a.force)

    print("\nNext:")
    print("  1) Edit registry.json: set account slug/user/role, cred_path and draft.signature.")
    print("     Your Name is a placeholder. Update template signatures to match after editing it.")
    print("  2) Capture each app password into DPAPI (config repo's capture-app-pw.ps1), Mode B.")
    print("  3) Copy rules/_personal_layer.json.template -> _personal_layer.json, fill VIPs.")
    print("     Version rules, templates and runtime DATA only in the PRIVATE companion.")
    print("  4) export %s=%s   (or use the default path)" % (env_var(), out))
    print("  5) python scripts/verify_config.py   # doctor: confirms the config is ready")
    print("  6) Optional, topic labeling: fill in rules/taxonomy.md, rules/sender_map.json and")
    print("     rules/labels.json, then flip registry.json's topic_labeling.enabled to true.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
