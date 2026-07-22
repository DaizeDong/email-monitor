#!/usr/bin/env python3
"""email-monitor agent classifier — judge each new mail with a background LLM, cheapest first.

Instead of scoring offline signals, we feed sender + subject + full body to a model and let it decide
the response-obligation tier the way a person would. Providers are tried in a cost-ordered CHAIN and
the first one that returns a parseable verdict wins:

  1. codex   -- OpenAI Codex CLI (`codex exec`), our least-used quota -> effectively spare capacity
  2. cc      -- Claude Code headless via a hosted gateway
  3. claude  -- plain Claude Code headless (direct) -> last resort

  priority : URGENT | ACTION | FYI | NOISE   (only URGENT/ACTION alert)
  label    : short semantic tag

Design:
  - Prompt fed on STDIN (bodies are large; keeps argv clean, dodges Windows cmdline limits).
  - Absolute binary paths (a scheduled task runs with a minimal PATH); `.cmd` launched via `cmd /c`.
  - codex writes its final message with `-o <file>` (clean, no reasoning preamble); cc/claude use
    `--output-format json` and we unwrap the `result` field.
  - Never raises: any provider failure (missing binary, timeout, unparseable output) is skipped and
    the next provider is tried; if all fail, returns None so the caller falls back to em_classify.
Transport (the codex -> cc -> claude chain + headless footguns) is the shared `llmcall` package
(pip dependency, see the repo requirements.txt); the domain logic here is stdlib.
"""
import json
import re
import sys

VALID = ("URGENT", "ACTION", "FYI", "NOISE")
BODY_CHARS = 12000            # trim body handed to the model (subject/sender stay full)
DEFAULT_CHAIN = ["codex", "cc", "claude"]

# Transport (the codex -> cc -> claude chain + every headless footgun: read-only codex, --ephemeral,
# absolute-path fallback, MCP off, json-envelope unwrap, single model/effort source) now lives in the
# shared `llmcall` package. This module keeps only the email-triage DOMAIN logic below: the prompt,
# JSON extraction, and verdict normalization.
from llmcall import call as _llmcall  # noqa: E402


# ---------- prompt + parsing (pure, unit-tested) ----------

def build_prompt(msg, owner=""):
    frm = (msg.get("from") or "").strip()
    subj = (msg.get("subject") or "").strip()
    body = (msg.get("body") or "").strip()
    if len(body) > BODY_CHARS:
        body = body[:BODY_CHARS] + "\n...[truncated]"
    lu = "yes" if msg.get("list_unsubscribe") else "no"
    owner_line = ("The mailbox owner: %s\n" % owner) if owner else ""
    return (
        "You triage an inbox. Judge ONE email by response-obligation: how likely the owner must "
        "personally act, not by topic. Be decisive and calibrated; most bulk/marketing/automated "
        "mail is NOISE, genuine personal or account-critical mail is ACTION/URGENT.\n"
        + owner_line +
        "\nTiers:\n"
        "- URGENT: needs action very soon; deadline today, payment failed, account suspended, "
        "security alert, time-critical personal request.\n"
        "- ACTION: the owner should personally reply or do something, but not same-hour "
        "(a real person asking, a form to sign, an interview, a bill to pay).\n"
        "- FYI: worth seeing, no action required (receipts, confirmations, FYI notices).\n"
        "- NOISE: newsletters, promotions, social notifications, automated noise.\n"
        "\nEmail:\n"
        "From: %s\nSubject: %s\nHas List-Unsubscribe header: %s\n\nBody:\n%s\n"
        "\nAlso write `summary_zh`: ONE short sentence in **Simplified Chinese** (<= 30 chars) that "
        "the owner reads on their phone instead of the subject line. Say WHAT it is and WHAT they "
        "must do -- concrete and specific ('订阅账户支付方式未填完,下次扣款前要补'), never "
        "vague ('有一封重要邮件'). Keep a real deadline or amount if there is one. For NOISE, one word "
        "is enough ('推广'). NEVER put a verification code, password, token, API key or full URL in "
        "it -- say '(见邮箱)' instead.\n"
        "\nReturn ONLY a compact JSON object, no prose, no code fence:\n"
        '{\"priority\":\"URGENT|ACTION|FYI|NOISE\",\"label\":\"<short semantic tag>\",'
        '\"summary_zh\":\"<=30 Chinese chars>\",\"reason\":\"<=12 words\",\"confidence\":0.0}\n'
        % (frm, subj, lu, body or "(empty)")
    )


def _extract_json(text):
    """Pull the first balanced {...} JSON object out of arbitrary model text."""
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except Exception:
                        break
        start = text.find("{", start + 1)
    return None


def _normalize(verdict, msg, tier="agent"):
    if not isinstance(verdict, dict):
        return None
    pr = str(verdict.get("priority", "")).strip().upper()
    if pr not in VALID:
        return None
    label = str(verdict.get("label") or "notification").strip()[:40] or "notification"
    reason = str(verdict.get("reason") or "").strip()[:120]
    # summary_zh is what the owner actually reads in the Discord push (see em_alert). It may be
    # absent when a provider ignores the field -- callers must fall back to the redacted subject.
    summary = str(verdict.get("summary_zh") or "").strip()[:60]
    try:
        conf = round(float(verdict.get("confidence", 0)), 3)
    except (TypeError, ValueError):
        conf = None
    return {"priority": pr, "label": label, "tier": tier, "summary_zh": summary,
            "reason": reason or "agent", "score": conf, "needs_l2": False}


def _valid_verdict(text):
    """The old per-provider loop's advance condition, expressed as an llmcall extract= hook: return the
    verdict object IFF it is a dict whose priority (case-insensitive) is in VALID, else None (a provider
    miss). Uses THIS module's fence-aware _extract_json so a ```json-fenced reply still parses."""
    obj = _extract_json(text)
    if isinstance(obj, dict) and str(obj.get("priority", "")).strip().upper() in VALID:
        return obj
    return None


def classify(msg, chain=None, providers=None, timeout=180, owner="", log=None):
    """Try providers in `chain` order via the shared llmcall transport; return the first parseable +
    valid verdict, else None.

    chain     : list of provider names, e.g. ["codex","cc","claude"] (cost-ordered).
    providers : accepted for signature compatibility but IGNORED; model/effort now resolve from one
                source (~/.codex/config.toml) inside llmcall.
    log       : optional callable(str) for diagnostics (which provider answered / failed)."""
    chain = chain or DEFAULT_CHAIN
    prompt = build_prompt(msg, owner)
    # One llmcall pass with extract=_valid_verdict: a reply whose priority is out-of-vocab counts as a
    # provider miss, so llmcall retries the SAME provider once (a self-correction the old loop lacked)
    # and only then falls through to the next -- the loop's advance-on-domain-invalid, preserved and
    # raised. log= reports each attempt; _normalize (domain coercion) still runs on the winning object.
    r = _llmcall(prompt, chain=chain, extract=_valid_verdict, timeout=timeout, log=log)
    if not r:
        return None
    verdict = _normalize(r.data, msg, tier=r.provider)
    if verdict and log:
        log("classify: %s -> %s (%s)" % (r.provider, verdict["priority"], verdict["label"]))
    return verdict


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--chain", default=",".join(DEFAULT_CHAIN),
                    help="comma-separated provider order (default: codex,cc,claude)")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--owner", default="")
    ap.add_argument("--codex-model", default="gpt-5.6-sol")
    ap.add_argument("--codex-reasoning", default="max")
    ap.add_argument("--claude-model", default="claude-opus-4-8")
    a = ap.parse_args()
    providers = {"codex": {"model": a.codex_model, "reasoning": a.codex_reasoning},
                 "cc": {"model": a.claude_model}, "claude": {"model": a.claude_model}}
    msg = json.loads(sys.stdin.read())
    out = classify(msg, [c.strip() for c in a.chain.split(",") if c.strip()],
                   providers, a.timeout, a.owner, log=lambda m: print(m, file=sys.stderr))
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out else 1


if __name__ == "__main__":
    sys.exit(main())
