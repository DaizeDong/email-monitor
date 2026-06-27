# email-monitor

Auto-monitor your inboxes: classify, alert, archive, draft, and summarize -- proven, not just generated.

[![Claude Code Skill](https://img.shields.io/badge/Claude%20Code-Skill-orange?style=flat)](https://docs.anthropic.com/en/docs/claude-code)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Languages](https://img.shields.io/badge/Languages-EN%20%2F%20CN-blue?style=flat)](#languages)
[![Roadmap](https://img.shields.io/badge/Roadmap-v0.1.1-purple?style=flat)](ROADMAP.md)

[English](README.md) | [中文版](README_CN.md)

---

## ⭐ Read this first -- the design philosophy

email-monitor is a **thin orchestration skill**. It does not build a new mail store, a new scheduler,
or a new notifier. It reuses three substrates already on the machine -- the Gmail IMAP toolchain, the
schedule-reminder task pool, and the Discord relay -- and adds only the missing seam: an incremental
watch, a classify/draft orchestrator, and archive/summary hooks. Two lines are absolute: **a reply is
never auto-sent** (drafts only; the user clicks Send), and **mail bodies never leave the local model**
(Discord gets a redacted title; the public repo stores no PII).

📜 **[Read the full design philosophy -> PHILOSOPHY.md](PHILOSOPHY.md)**

---

## What it is (and isn't)

- **It is:** an unattended inbox triage loop -- watch new mail (UID-incremental, read-only), classify
  by importance (rules -> cheap scoring -> LLM only for the uncertain few), alert important ones to
  Discord, archive noise, track each affair as a task in the schedule-reminder pool, draft concise
  ASCII replies for your review, and send a daily summary.
- **It isn't:** an auto-sender (it only drafts), a second task database (it uses schedule-reminder),
  or a bulk inbox cleaner (use `gmail-imap-label.py` directly for that).

## Install

```
/plugin install github:DaizeDong/email-monitor
```

Or clone manually:

```bash
git clone https://github.com/DaizeDong/email-monitor.git ~/.claude/plugins/email-monitor
```

You also need a private companion config repo (`email-monitor-config`) holding account topology, rules,
templates, and DPAPI pointers (secrets gitignored). See `reference/summary-and-deploy.md`.

## Quick start

```bash
# one dry tick (no alert / no archive), shows what it would do
python skills/email-monitor/scripts/em_tick.py --config <path>/registry.json --dry
# install the heartbeat (absolute pythonw pinned)
pwsh skills/email-monitor/scripts/register-task.ps1 -Config <path>/registry.json
```

## Config

`email-monitor` is **config-bearing** — it reads per-user/per-machine state (account topology,
classification rules, draft templates, DPAPI credential pointers) from a **separate, private**
companion config repo (`email-monitor-config`). Full contract: **[CONFIG.md](CONFIG.md)**.

- **Mount (discovery order):** `$EMAIL_MONITOR_CONFIG` → `$EMAIL_MONITOR_CONFIG_DIR` →
  `~/.email-monitor-config/` → `~/.config/email-monitor-config/`, then `<dir>/registry.json`. An
  explicit `--config <registry.json>` overrides discovery; if nothing resolves the skill says so and
  exits cleanly (no crash).
- **First time:**
  ```bash
  python scripts/init_config.py    # stamp a conformant skeleton (deterministic)
  export EMAIL_MONITOR_CONFIG=~/.email-monitor-config    # or pass --out <dir> to init
  # edit registry.json, capture app passwords into DPAPI (Mode B), fill _personal_layer.json
  python scripts/verify_config.py   # doctor: PASS/FAIL, names what is missing
  ```
- **Switch configs (hot-swap):** point the env var at another config dir — configs are
  self-contained (`cred_path` uses `~`), no other change:
  `export EMAIL_MONITOR_CONFIG=~/configs/work` ↔ `~/configs/personal`.
- **Secrets:** Mode B — `secrets/*` is gitignored and never enters git; real app passwords stay in
  DPAPI (`~/.local/secrets/gmail-<slug>.cred`), the repo keeps only pointers. Back up out-of-band.

## How to invoke

"monitor my email", "triage my inbox", "draft a reply to this", "what important mail came in",
"daily email summary". The heartbeat runs unattended once registered.

## Example output

A redacted Discord ping `[URGENT] user1: payment failed account`, a pool task
`Reply to mail re Acme start date`, and a clean Gmail draft ending exactly `Daize Dong`.

## Limitations

v0.1: L2 LLM classification and reply prose are produced by the calling session (the skill provides the
deterministic gate, templates, and routing). Gmail-only IMAP. State/status-change monitoring (read,
relabel, delete) is roadmap v0.4.

## Languages

English (`README.md`, authoritative) · 中文 (`README_CN.md`)

## Roadmap · Contributing · License

See [ROADMAP.md](ROADMAP.md) · [CONTRIBUTING.md](CONTRIBUTING.md) · [LICENSE](LICENSE) (MIT).
