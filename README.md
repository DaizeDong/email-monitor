# email-monitor

Incremental inbox triage with reviewable drafts and durable action tracking.

[![Claude Code Skill](https://img.shields.io/badge/Claude%20Code-Skill-orange?style=flat)](https://docs.anthropic.com/en/docs/claude-code)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Languages](https://img.shields.io/badge/Languages-EN%20%2F%20CN-blue?style=flat)](README_CN.md)
[![Roadmap](https://img.shields.io/badge/Roadmap-v0.2.0-purple?style=flat)](ROADMAP.md)

[English](README.md) | [中文版](README_CN.md)

---

## ⭐ Design Philosophy

email-monitor is a **thin orchestration skill**. It does not build a new mail store, a new scheduler,
or a new notifier. It reuses three substrates already on the machine -- the Gmail IMAP toolchain, the
schedule-reminder task pool, and the Discord relay -- and adds only the missing seam: an incremental
watch, a classify/draft orchestrator, and archive/summary hooks. **Replies remain drafts for your
review.** Default agent classification includes mail content, including the body, in prompts sent
through the installed `llmcall` routing policy. That policy may use external providers; the default
does not guarantee local model processing. Discord alerts contain a redacted one-line gist.

For enforced local-only model processing, set `runtime.local_only=true`,
`classifier.mode="heuristic"`, and `topic_labeling.enabled=false` in the private registry.
The runtime rejects agent classification and topic models when their locality cannot be verified.
Mail access and explicitly configured notifications still use their respective services.

Reusing these substrates avoids duplicate ledgers but makes their availability a
prerequisite. Importance, topic labels and archive actions stay separate so adding
a label cannot silently hide a message. Default model routing trades external
processing for broader classification; local heuristics reduce that exposure and
give up model classification.

📜 **[Read the full design philosophy -> PHILOSOPHY.md](PHILOSOPHY.md)**

---

## What it is (and isn't)

- **It is:** an inbox triage loop: watch new mail with a read-only UID cursor, classify by importance
  through the default agent route or a selected heuristic, alert important mail, and archive noise.
  The optional schedule-reminder base adds pool tracking and daily summaries. The calling session
  can draft replies using the configured signature and language for your review.
- **It isn't:** an auto-sender (it only drafts), a second task database (it uses schedule-reminder),
  or a bulk inbox cleaner (use `gmail-imap-label.py` directly for that).

## Install

```
/plugin install github:DaizeDong/email-monitor
```

Or clone manually:

```bash
git clone --recurse-submodules https://github.com/DaizeDong/email-monitor.git ~/.claude/plugins/email-monitor
```

Use Python 3.11 or later. Install dependencies in the interpreter selected for the heartbeat:

```bash
python -m pip install -r requirements.txt
```

The declared llmcall Git source requires authorized repository access through your host's existing
credential or SSH setup. An anonymous checkout can return "Repository not found". The package named
`llmcall` on PyPI is a different project; do not substitute it for this dependency.

If your host supplies an approved llmcall wheel, use this alternate path instead of the requirements
command above. Replace the example wheel path with the actual file supplied by your host:

```bash
python -m pip install --no-index "/path/to/llmcall-0.2.0-py3-none-any.whl"
python -m pip install "tzdata; sys_platform == 'win32'"
python -c "from llmcall import Result, active_chain, call; print('llmcall API imports successfully')"
```

The requirements command still fetches its declared Git source even after a wheel is installed.
The configuration doctor checks the selected interpreter. Routing, credentials, timeouts and
fallback remain owned by the installed host package; installing Email Monitor does not configure them.

You also need a private companion config repo (`email-monitor-config`) holding account topology, rules,
templates, versioned runtime DATA and DPAPI pointers (credentials kept separate). See
[summary and deployment](skills/email-monitor/reference/summary-and-deploy.md).

## Quick start

Review the model-routing choice above and configure a PRIVATE companion before processing mail.
A dry tick plans actions without applying them; it still reads and classifies messages according to
your configured routing policy. Test with a controlled mailbox before registering unattended work.

```bash
# one dry tick (no alert / no archive), shows what it would do
python skills/email-monitor/scripts/em_tick.py --config <path>/registry.json --dry
# install the heartbeat (absolute pythonw pinned)
pwsh skills/email-monitor/scripts/register-task.ps1 -Config <path>/registry.json
```

## Config

`email-monitor` is **config-bearing**, it reads per-user/per-machine state (account topology,
classification rules, draft templates, DPAPI credential pointers) from a **separate, private**
companion config repo (`email-monitor-config`). Full contract: **[CONFIG.md](CONFIG.md)**.

- **Mount (discovery order):** `$EMAIL_MONITOR_CONFIG` → `$EMAIL_MONITOR_CONFIG_DIR` →
  the pinned Guards companion-root convention (including sibling and legacy locations),
  then `<dir>/registry.json`. An
  explicit `--config <registry.json>` overrides discovery. Missing configuration produces structured
  failure and a nonzero exit without sending an alert.
- **First time:** create or clone a verified PRIVATE Git companion and point `EMAIL_MONITOR_CONFIG`
  at it before initializing. Runtime DATA remains versioned there; unverified storage is rejected.
  ```bash
  export EMAIL_MONITOR_CONFIG=<private-companion>
  python scripts/init_config.py    # stamp a conformant skeleton (deterministic)
  # edit registry.json, capture app passwords into DPAPI (Mode B), fill _personal_layer.json
  python scripts/verify_config.py   # doctor: PASS/FAIL, names what is missing
  ```
- **Switch configs (hot-swap):** point the env var at another config dir, configs are
  self-contained (`cred_path` uses `~`), no other change:
  `export EMAIL_MONITOR_CONFIG=~/configs/work` ↔ `~/configs/personal`.
- **Secrets:** Mode B, `secrets/*` is gitignored and never enters git; real app passwords stay in
  DPAPI (`~/.local/secrets/gmail-<slug>.cred`), the repo keeps only pointers. Back up out-of-band.

## Topic labeling (opt-in, add-only)

email-monitor can additionally attach topic labels to new mail -- what a message is about, not just
how important it is. This is a separate, off-by-default capability: `topic_labeling.enabled` in
`registry.json` (default `false`), documented in **[CONFIG.md](CONFIG.md)**. Every label decision
must quote a verbatim span from the sender or subject as evidence; a proposed label whose evidence
does not check out is dropped rather than guessed, and a message with no clear evidence gets no
label at all. Labels are added to the message; the write path can never remove it from the inbox --
adding a label and hiding a message are different decisions. The label taxonomy itself (what labels
exist, which senders map to which label) is DATA, not code: it lives only in the private companion
config, never in this public repo.

## How to invoke

"monitor my email", "triage my inbox", "draft a reply to this", "what important mail came in",
"daily email summary". The heartbeat runs unattended once registered.

## Example output

Synthetic messages and draft examples are generated by `tools/make_fixtures.py` in
`skills/email-monitor/tests/reliability.json`. Alerts use a redacted gist, and drafts use the
signature and language selected in the private configuration. Review drafts before sending.

## Limitations

Offline tests verify these contracts using synthetic messages and intercepted effects. They do not
establish live classification quality, successful delivery, or unattended scheduler readiness.

Default importance classification runs through llmcall; the heuristic's `needs_l2` flag does not
trigger a separate heartbeat model call. Reply prose is produced by the calling session, whose
transport requires its own routing review. Gmail-only IMAP. State/status-change monitoring (read,
relabel, delete) remains roadmap v0.4.

## Languages

English (`README.md`, authoritative) · 中文 (`README_CN.md`)

## Roadmap · Contributing · License

See [ROADMAP.md](ROADMAP.md) · [CONTRIBUTING.md](CONTRIBUTING.md) · [LICENSE](LICENSE) (MIT).
