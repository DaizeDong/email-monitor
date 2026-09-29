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
    parts = re.split(r"(?<=[.!?])\s+|(?<=[。！？])\s*", text.strip())
    return [p for p in parts if p.strip()]


def draft_config(config=None):
    """Validate selected draft constraints; missing identity raises ValueError.

    Pass a draft object or a registry containing one. There is no implicit signature.
    """
    if config is None:
        raise ValueError("draft.signature is required; select a draft configuration")
    if not isinstance(config, dict):
        raise ValueError("draft configuration must be an object")
    if "draft" in config:
        config = config["draft"]
    if not isinstance(config, dict):
        raise ValueError("draft configuration must be an object")
    signature = config.get("signature")
    language = config.get("language", "en")
    style = config.get("style", {})
    if not isinstance(signature, str) or not signature.strip() or "\n" in signature or "\r" in signature:
        raise ValueError("draft.signature is required and must be a nonempty single line")
    if language not in ("en", "zh", "any"):
        raise ValueError("draft.language must be en, zh or any")
    if not isinstance(style, dict):
        raise ValueError("draft.style must be an object")
    for key, value in style.items():
        if key in ("max_lines", "max_sentences"):
            if type(value) is not int or value < 1:
                raise ValueError("draft.style." + key + " must be a positive integer")
        elif key == "allow_markdown":
            if type(value) is not bool:
                raise ValueError("draft.style.allow_markdown must be a boolean")
        else:
            raise ValueError("unknown draft style constraint: " + key)
    return {"signature": signature, "language": language, "style": style}


def lint(text, profile, config=None):
    profile = profile if profile in LINE_CAPS else "business"
    settings = draft_config(config)
    style = settings["style"]
    viol = []

    # 1) ASCII only
    nonascii = [(i, ch) for i, ch in enumerate(text) if ord(ch) > 127]
    if nonascii and settings["language"] == "en":
        sample = ", ".join("U+%04X@%d" % (ord(c), i) for i, c in nonascii[:5])
        viol.append("non-ascii chars: %s" % sample)

    # 2) markdown
    if MARKDOWN.search(text) and not style.get("allow_markdown", False):
        viol.append("markdown syntax present (# * ` [ ] > _)")

    # 3) em/en dash
    if EMDASH.search(text):
        viol.append("em-dash / en-dash present (use plain hyphen or rewrite)")

    # 4) curly quotes
    if CURLY.search(text) and settings["language"] == "en":
        viol.append("curly/smart quotes present (use straight quotes)")

    # 5) send markers
    if SEND_MARKERS.search(text):
        viol.append("send/SMTP marker present (drafts must never auto-send)")

    # 6) The selected signature must be the last non-empty line.
    lines = [ln.rstrip() for ln in text.splitlines()]
    nonempty = [ln for ln in lines if ln.strip()]
    if not nonempty or nonempty[-1].strip() != settings["signature"]:
        last = nonempty[-1].strip() if nonempty else "<empty>"
        viol.append("signature must be exactly %r (got: %r)" % (settings["signature"], last))

    # 7) line cap
    body_lines = len([ln for ln in lines if ln.strip()])
    line_cap = style.get("max_lines", LINE_CAPS[profile])
    if body_lines > line_cap:
        viol.append("line count %d > cap %d for profile %s"
                    % (body_lines, line_cap, profile))

    # 8) sentence cap (exclude signature line + greeting)
    body_for_sent = "\n".join(nonempty[:-1]) if len(nonempty) > 1 else ""
    n_sent = len(split_sentences(body_for_sent))
    sentence_cap = style.get("max_sentences", SENTENCE_CAPS[profile])
    if n_sent > sentence_cap:
        viol.append("sentence count %d > cap %d for profile %s"
                    % (n_sent, sentence_cap, profile))

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
