#!/usr/bin/env python3
"""email-monitor agent classifier — hand each new mail to `claude -p` (headless) for a judgment.

This replaces the keyword/heuristic pipeline (em_classify) as the primary path: instead of scoring
offline signals, we feed sender + subject + full body to a Claude model and let it decide the
response-obligation tier. The model reads the mail the way a person would.

  priority : URGENT | ACTION | FYI | NOISE   (same axis as em_classify; only URGENT/ACTION alert)
  label    : short semantic tag (bill, calendar, security, personal, newsletter, ...)

Design notes:
  - Prompt fed on STDIN (bodies are large; keeps argv clean and avoids Windows cmdline limits).
  - `--output-format json` returns an envelope {type:"result", result:"<model text>", ...}; the
    model text is itself our JSON verdict. We parse both layers defensively.
  - Never raises: any failure (missing binary, timeout, unparseable output) returns None so the
    caller can fall back to the deterministic em_classify heuristic. Fail-safe, never fail-silent.
  - No secrets on argv; body is only sent to the model via the local `claude` CLI (cc gateway).
Stdlib only.
"""
import json
import os
import re
import shutil
import subprocess
import sys

_NOWINDOW = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
VALID = ("URGENT", "ACTION", "FYI", "NOISE")
BODY_CHARS = 12000  # trim body handed to the model (subject/sender are always full)


def find_claude(explicit=None):
    """Locate the claude CLI. Explicit path wins; then PATH; then the known per-user install."""
    if explicit and os.path.isfile(explicit):
        return explicit
    found = shutil.which("claude")
    if found:
        return found
    for c in (os.path.expanduser(r"~/.local/bin/claude.exe"),
              os.path.expanduser(r"~/.local/bin/claude")):
        if os.path.isfile(c):
            return c
    return None


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
        "\nReturn ONLY a compact JSON object, no prose, no code fence:\n"
        '{\"priority\":\"URGENT|ACTION|FYI|NOISE\",\"label\":\"<short semantic tag>\",'
        '\"reason\":\"<=12 words\",\"confidence\":0.0}\n'
        % (frm, subj, lu, body or "(empty)")
    )


def _extract_json(text):
    """Pull the first balanced {...} JSON object out of arbitrary model text."""
    if not text:
        return None
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
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


def _normalize(verdict, msg):
    if not isinstance(verdict, dict):
        return None
    pr = str(verdict.get("priority", "")).strip().upper()
    if pr not in VALID:
        return None
    label = str(verdict.get("label") or "notification").strip()[:40] or "notification"
    reason = str(verdict.get("reason") or "").strip()[:120]
    try:
        conf = round(float(verdict.get("confidence", 0)), 3)
    except (TypeError, ValueError):
        conf = None
    return {"priority": pr, "label": label, "tier": "agent",
            "reason": reason or "agent", "score": conf, "needs_l2": False}


def classify(msg, model="claude-opus-4-8", timeout=120, claude_bin=None, owner=""):
    """Return a verdict dict {priority,label,tier,reason,score} or None on any failure."""
    binp = find_claude(claude_bin)
    if not binp:
        return None
    prompt = build_prompt(msg, owner)
    cmd = [binp, "-p", "--model", model, "--output-format", "json"]
    try:
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                           encoding="utf-8", timeout=timeout, **_NOWINDOW)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if p.returncode != 0 or not (p.stdout or "").strip():
        return None
    # First layer: the --output-format json envelope.
    inner = p.stdout
    try:
        env = json.loads(p.stdout)
        if isinstance(env, dict) and "result" in env:
            inner = env.get("result") or ""
    except Exception:
        inner = p.stdout  # not an envelope; treat stdout as the verdict text
    return _normalize(_extract_json(inner), msg)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-opus-4-8")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--claude-bin", default=None)
    ap.add_argument("--owner", default="")
    a = ap.parse_args()
    msg = json.loads(sys.stdin.read())
    out = classify(msg, a.model, a.timeout, a.claude_bin, a.owner)
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out else 1


if __name__ == "__main__":
    sys.exit(main())
