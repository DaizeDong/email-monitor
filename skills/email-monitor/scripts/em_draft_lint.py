#!/usr/bin/env python3
"""email-monitor draft linter — CLI entrypoint (thin shim over em_lint_rules).

The deterministic rule engine (signals #4 + #5) lives in em_lint_rules.py, which
imports only `re` so the self-evolve patch gate can actually patch the rules.
This file is just the argparse/stdin/stdout CLI wrapper and re-exports the rule
symbols for backward compatibility (callers/tests still do `em_draft_lint.lint`).

Hard draft rules (from user feedback_email_drafts_plaintext + ARCHITECTURE 2.5):
  - body is plain ASCII (no non-ASCII bytes at all)
  - no markdown syntax: # * ` [ ] > _ (heading/bold/code/link/quote markers)
  - no em-dash / en-dash; no curly quotes
  - no emoji (covered by ASCII rule)
  - signature ends exactly with "Daize Dong" (no title / company / slogan)
  - body contains NO send call / SMTP marker (drafts are never auto-sent)
  - line/sentence caps per profile (business<=20 lines / dealer<=10 / support<=12 / personal<=15)
  - AI-flavor kill-list words/phrases -> 0 hits
  - banned sentence shapes (negation-parallel, opening throat-clearing) -> 0 hits

Usage:
  python em_draft_lint.py --file draft.txt --profile dealer [--json]
  echo "...." | python em_draft_lint.py --profile business --json
Exit 0 = clean, 1 = violations. Stdlib only.
"""
import argparse
import json
import sys

# Re-export the pure rule engine (backward-compatible public surface).
from em_lint_rules import (  # noqa: F401
    LINE_CAPS,
    SENTENCE_CAPS,
    KILL_WORDS,
    KILL_PHRASES,
    BANNED_SHAPES,
    MARKDOWN,
    EMDASH,
    CURLY,
    SEND_MARKERS,
    split_sentences,
    lint,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="path to draft text; omit to read stdin")
    ap.add_argument("--profile", default="business",
                    choices=["business", "dealer", "support", "personal"])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.file:
        with open(a.file, "r", encoding="utf-8") as f:
            text = f.read()
    else:
        text = sys.stdin.read()
    viol = lint(text, a.profile)
    if a.json:
        print(json.dumps({"clean": not viol, "profile": a.profile,
                          "violations": viol}, ensure_ascii=False))
    else:
        if viol:
            print("DRAFT REJECTED (%d violation(s)):" % len(viol))
            for v in viol:
                print("  - " + v)
        else:
            print("DRAFT CLEAN (profile=%s)" % a.profile)
    return 1 if viol else 0


if __name__ == "__main__":
    sys.exit(main())
