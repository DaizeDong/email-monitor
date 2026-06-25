#!/usr/bin/env python3
"""email-monitor draft-lint RULES — pure, stdlib-`re`-only rule engine.

This module holds the deterministic compliance + AI-flavor detection logic
(self-evolve signals #4 + #5). It deliberately imports ONLY `re` so it carries
no argparse/sys surface: the CLI entrypoint lives in em_draft_lint.py, which
re-exports everything here. Keeping the rule engine import-clean lets the
self-evolve patch gate (whitelist = re/json/... only) actually patch it.

Every rule is a regex/scan that returns a hard pass/fail. No model self-grade.
"""
import re

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
    "looking forward to hearing",
    "thank you for reaching out",
    "i hope you are doing well",
    "i hope you're doing well",
    "i hope all is well",
    "hope all is well",
    "hope this message finds you well",
    "please let me know if you have any questions",
]
# banned sentence shapes (regex over lowercased body)
BANNED_SHAPES = [
    (r"\bit'?s not\b[^.?!]{1,60}\bit'?s\b", "negation-parallel (it's not X, it's Y)"),
    (r"\bnot (just|only)\b[^.?!]{1,60}\bbut (also)?\b", "not-just-but-also parallelism"),
    (r"(?:^|\n)[ \t]*(?:additionally|notably|importantly|ultimately|consequently|"
     r"subsequently|therefore|nevertheless|nonetheless|conversely|accordingly)\s*,",
     "transition-adverb opener (AI filler)"),
    (r"\b(please\s+)?do(?:n'?t| not)\s+hesitate\b", "do-not-hesitate hedge"),
    (r"\bfeel free to (reach out|contact|ask|email|call)\b", "feel-free-to boilerplate"),
    (r"\bshould you have any (questions|concerns|issues)\b", "should-you-have-any boilerplate"),
    (r"\b(i\s+)?look forward to (your (response|reply)|hearing)\b", "look-forward-to closing boilerplate"),
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
