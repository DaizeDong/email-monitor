#!/usr/bin/env python3
"""email-monitor draft linter — deterministic, no-model compliance + AI-flavor gate.

A draft is the compliance object. This linter is the program-judged regression signal
(self-evolve signals #4 + #5): it never trusts the model's self-assessment. Every rule is a
regex/scan that returns a hard pass/fail.

Hard draft rules (from user feedback_email_drafts_plaintext + ARCHITECTURE §2.5):
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
import re
import sys

LINE_CAPS = {"business": 20, "dealer": 10, "support": 12, "personal": 15}
SENTENCE_CAPS = {"business": 12, "dealer": 8, "support": 9, "personal": 12}

# AI-flavor word/phrase kill-list (lowercased substring or word match). Quarterly review.
KILL_WORDS = [
    "delve", "leverage", "foster", "empower", "streamline", "elevate",
    "seamless", "robust", "cutting-edge", "transformative", "pivotal",
    "comprehensive", "tapestry", "landscape", "realm", "beacon",
    "furthermore", "moreover", "in conclusion", "it is worth noting",
    "navigate the", "underscore", "facilitate", "utilize", "synergy",
    "holistic", "paradigm", "game-changer", "unlock the", "supercharge",
]
KILL_PHRASES = [
    "in today's fast-paced world",
    "i hope this email finds you well",
    "i hope this finds you well",
    "i wanted to reach out",
    "i am reaching out",
    "the answer lies in",
    "here's the kicker",
    "at the end of the day",
    "needless to say",
]
# banned sentence shapes (regex over lowercased body)
BANNED_SHAPES = [
    (r"\bit'?s not\b[^.?!]{1,60}\bit'?s\b", "negation-parallel (it's not X, it's Y)"),
    (r"\bnot (just|only)\b[^.?!]{1,60}\bbut (also)?\b", "not-just-but-also parallelism"),
]

MARKDOWN = re.compile(r"(^|[^\w])([#*`>_]|\[[^\]]*\]\()")
EMDASH = re.compile(r"[‒–—―]")  # figure/en/em/horizontal-bar dashes
CURLY = re.compile(r"[‘’“”]")
SEND_MARKERS = re.compile(
    r"(send-gmail\.ps1|smtplib|\.sendmail\(|server\.send|SMTP\(|--send\b)", re.I
)


def split_sentences(text):
    # crude but deterministic: split on . ! ? followed by space/eol
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p.strip()]


def lint(text, profile):
    profile = profile if profile in LINE_CAPS else "business"
    viol = []

    # 1) ASCII only
    nonascii = [(i, ch) for i, ch in enumerate(text) if ord(ch) > 127]
    if nonascii:
        sample = ", ".join("U+%04X@%d" % (ord(c), i) for i, c in nonascii[:5])
        viol.append("non-ascii chars: %s" % sample)

    # 2) markdown
    if MARKDOWN.search(text):
        viol.append("markdown syntax present (# * ` [ ] > _)")

    # 3) em/en dash
    if EMDASH.search(text):
        viol.append("em-dash / en-dash present (use plain hyphen or rewrite)")

    # 4) curly quotes
    if CURLY.search(text):
        viol.append("curly/smart quotes present (use straight quotes)")

    # 5) send markers
    if SEND_MARKERS.search(text):
        viol.append("send/SMTP marker present (drafts must never auto-send)")

    # 6) signature exactly "Daize Dong" as last non-empty line
    lines = [ln.rstrip() for ln in text.splitlines()]
    nonempty = [ln for ln in lines if ln.strip()]
    if not nonempty or nonempty[-1].strip() != "Daize Dong":
        last = nonempty[-1].strip() if nonempty else "<empty>"
        viol.append("signature must be exactly 'Daize Dong' (got: %r)" % last)

    # 7) line cap
    body_lines = len([ln for ln in lines if ln.strip()])
    if body_lines > LINE_CAPS[profile]:
        viol.append("line count %d > cap %d for profile %s"
                    % (body_lines, LINE_CAPS[profile], profile))

    # 8) sentence cap (exclude signature line + greeting)
    body_for_sent = "\n".join(nonempty[:-1]) if len(nonempty) > 1 else ""
    n_sent = len(split_sentences(body_for_sent))
    if n_sent > SENTENCE_CAPS[profile]:
        viol.append("sentence count %d > cap %d for profile %s"
                    % (n_sent, SENTENCE_CAPS[profile], profile))

    low = text.lower()
    # 9) kill-list words
    hits = sorted({w for w in KILL_WORDS if re.search(r"\b%s\b" % re.escape(w), low)})
    if hits:
        viol.append("AI kill-list words: %s" % ", ".join(hits))
    # 10) kill-list phrases
    ph = sorted({p for p in KILL_PHRASES if p in low})
    if ph:
        viol.append("AI kill-list phrases: %s" % "; ".join(ph))
    # 11) banned shapes
    for pat, label in BANNED_SHAPES:
        if re.search(pat, low):
            viol.append("banned sentence shape: %s" % label)

    return viol


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
