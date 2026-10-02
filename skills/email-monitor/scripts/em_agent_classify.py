#!/usr/bin/env python3
"""Judge response obligation from an email's sender, subject and bounded body.

Installed llmcall owns routing, model, timeout and fallback policy. This module
uses its judge mode and validates the domain verdict. A failed transport returns
None so the heartbeat can use its deterministic heuristic. Obsolete local model
controls raise a migration error before a request is made.

Priority is URGENT, ACTION, FYI or NOISE; only URGENT and ACTION alert.
"""
import json
import re
import sys

from em_runtime import reject_model_overrides

VALID = ("URGENT", "ACTION", "FYI", "NOISE")
BODY_CHARS = 12000            # trim body handed to the model (subject/sender stay full)
# Transport policy comes from the installed dependency.
try:
    from llmcall import call as _llmcall  # noqa: E402
except ImportError:
    _llmcall = None

# Date extraction is a sibling, stdlib-only module (email-monitor's own half of the optional
# dated-reminder co-op). Guard the import so a missing/odd em_dates can never break classification.
try:
    from em_dates import normalize_due_at  # noqa: E402
except Exception:  # pragma: no cover
    def normalize_due_at(raw, now=None):
        return None


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
        "must do, using a concrete action rather than a vague importance statement. "
        "Keep a stated deadline or amount if there is one. For NOISE, one word "
        "is enough. NEVER put a verification code, password, token, API key or full URL in "
        "it -- say '(见邮箱)' instead.\n"
        "\nAlso `due_at`: if the email states a SPECIFIC date/time the OWNER must personally keep -- a "
        "confirmed appointment, a bill or form due date, an interview time -- return it as ISO8601 "
        "(YYYY-MM-DDTHH:MM; add a timezone offset ONLY if the email itself gives one; a date with no "
        "clock time is fine). Otherwise null. Extract ONLY the owner's own "
        "appointment/deadline -- never a marketing 'sale ends' date or a date merely mentioned in "
        "passing.\n"
        "\nReturn ONLY a compact JSON object, no prose, no code fence:\n"
        '{\"priority\":\"URGENT|ACTION|FYI|NOISE\",\"label\":\"<short semantic tag>\",'
        '\"summary_zh\":\"<=30 Chinese chars>\",\"due_at\":\"<ISO8601 or null>\",'
        '\"reason\":\"<=12 words\",\"confidence\":0.0}\n'
        % (frm, subj, lu, body or "(empty)")
    )


def _extract_json(text):
    """Decode the first JSON container without recovering from nested fragments."""
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()).strip()
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        pass
    # A truncated outer object or array must not promote its example to a verdict.
    start = re.search(r"[\[{]", text)
    if start is None:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(text, start.start())
    except (ValueError, RecursionError):
        return None
    return value


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
    # The 60-char cut ends in an ellipsis: a clipped gist must not read as a complete sentence.
    summary = str(verdict.get("summary_zh") or "").strip()
    if len(summary) > 60:
        summary = summary[:59] + "…"
    try:
        conf = round(float(verdict.get("confidence", 0)), 3)
    except (TypeError, ValueError):
        conf = None
    # due_at: the concrete owner-facing date the model found (if any). We keep BOTH the raw model
    # string (`due_raw`) and a base-less normalization (`due_at`, absolute-ISO only). The caller
    # (em_tick) re-runs normalization with the mail's Date as `base`, so a relative phrase like
    # "by Friday" still resolves via em_duenorm -- which needs that base and isn't available here.
    due_raw = str(verdict.get("due_at") or "").strip() or None
    due_at = normalize_due_at(due_raw)
    return {"priority": pr, "label": label, "tier": tier, "summary_zh": summary,
            "due_at": due_at, "due_raw": due_raw, "reason": reason or "agent",
            "score": conf, "needs_l2": False}


def _valid_verdict(text):
    """The old per-provider loop's advance condition, expressed as an llmcall extract= hook: return the
    verdict object IFF it is a dict whose priority (case-insensitive) is in VALID, else None (a provider
    miss). Uses THIS module's fence-aware _extract_json so a ```json-fenced reply still parses."""
    obj = _extract_json(text)
    if isinstance(obj, dict) and str(obj.get("priority", "")).strip().upper() in VALID:
        return obj
    return None


def classify(msg, chain=None, providers=None, timeout=None, owner="", log=None):
    """Judge one message using installed llmcall policy; return a verdict or None.

    Legacy policy arguments accept only None. Configure model policy in llmcall.
    ``owner`` provides task context; ``log`` receives transport diagnostics.
    """
    reject_model_overrides(chain=chain, providers=providers, timeout=timeout)
    if _llmcall is None:
        if log:
            log("llmcall dependency unavailable")
        return None
    prompt = build_prompt(msg, owner)
    # Invalid domain output is a transport miss; llmcall owns retry and fallback policy.
    r = _llmcall(prompt, mode="judge", extract=_valid_verdict, log=log)
    if not r:
        return None
    verdict = _normalize(r.data, msg, tier=r.provider)
    if verdict and log:
        log("classify: %s -> %s (%s)" % (r.provider, verdict["priority"], verdict["label"]))
    return verdict


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner", default="")
    obsolete = ("chain", "timeout", "codex-model", "codex-reasoning", "claude-model")
    for option in obsolete:
        ap.add_argument("--" + option, help="obsolete; configure installed llmcall instead")
    a = ap.parse_args()
    try:
        reject_model_overrides(**{option: getattr(a, option.replace("-", "_")) for option in obsolete})
    except ValueError as error:
        ap.error(str(error))
    msg = json.loads(sys.stdin.buffer.read().decode("utf-8-sig", "replace"))
    out = classify(msg, owner=a.owner, log=lambda m: print(m, file=sys.stderr))
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out else 1


if __name__ == "__main__":
    sys.exit(main())
