#!/usr/bin/env python3
"""Round-2 PII补清 guard: scan the WHOLE git-tracked repo for real personal PII.

The earlier round-2 MED fix only scrubbed two acceptance fixtures; docs/scripts still
carried the operator's real Gmail account, employer, and residence tokens. This guard
scans every git-tracked file (not just fixtures) against a denylist of real private
identifiers and fails if any literal appears.

Program-judged, no model self-assessment. Pre-fix tree => FAIL; post-fix => PASS.
Run: pytest -q (from skills/email-monitor/).

Note on scope: the public byline "Daize Dong" / author "DaizeDong" is intentionally
NOT on this denylist. It is the Spec-v1-required public authorship of this repo
(MIT LICENSE (c) DaizeDong, plugin/author=DaizeDong) and the user-sanctioned,
linter-enforced draft signature contract -- public authorship, not a leaked private
identifier. Private operational identifiers (mail account, employer, residence,
phone, street address) are what must never appear.
"""
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))


def _repo_root():
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=HERE,
                             capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return None


# Denylist tokens are assembled from fragments so this scanner file does not
# self-match (defense-in-depth on top of the SCANNER_FILES exclusion below).
# Compared case-insensitively. Real private identifiers only.
PII_DENYLIST = [
    "exampleemp" + "loyer",          # real employer
    "the" + "ExampleResidence",         # real residence (property)
    "ExampleResidence",                 # real residence (token)
    "<account>" + "2019",         # real Gmail account slug
    "exampleslug" + "two",     # real Gmail account slug
    "exampleslug" + "three",             # real Gmail account slug
    "555-" + "0100",           # real phone (hyphenated)
    "5550" + "100",            # real phone (joined)
    "examplest" + "reet",        # real street
    "examplec" + "ity",         # real city
    "main " + "street",            # real street
]


def _private_denylist():
    """Extra tokens supplied by the operator's PRIVATE companion config, if it is present.

    A hardcoded list can only block what someone already thought of. It did not know the operator
    used a particular therapy platform, so a real provider name and a `@m.<vendor>.co` message-id
    reached this public repo through an example and a test fixture -- the scanner had nothing to
    match. Operators can therefore add their own sensitive words to `pii_denylist` in the private
    registry.json; those words never appear in this public repo (that is the whole point), so they
    are read at runtime and simply skipped when the config is absent (CI, other machines).
    """
    for p in (os.environ.get("EMAIL_MONITOR_CONFIG"),
              os.path.expanduser("~/.email-monitor-config/registry.json")):
        if not p or not os.path.isfile(p):
            continue
        try:
            import json
            with open(p, "r", encoding="utf-8") as f:
                extra = json.load(f).get("pii_denylist") or []
            return [str(t).lower() for t in extra if str(t).strip()]
        except Exception:
            return []
    return []


# Files that MUST contain denylist literals because they ARE the scanners.
SCANNER_FILES = {"test_no_real_pii_in_repo.py", "test_audit_round2.py"}

# Binary/text extensions to skip on decode failure are handled by try/except.


def _tracked_files(root):
    out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True)
    if out.returncode != 0:
        return []
    return [line for line in out.stdout.splitlines() if line.strip()]


def test_no_real_pii_in_repo():
    root = _repo_root()
    if not root:
        pytest.skip("not a git checkout")
    leaks = []
    for rel in _tracked_files(root):
        if os.path.basename(rel) in SCANNER_FILES:
            continue
        path = os.path.join(root, rel)
        try:
            text = open(path, encoding="utf-8").read().lower()
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable; PII denylist is ASCII text
        for tok in PII_DENYLIST + _private_denylist():
            if tok.lower() in text:
                leaks.append("%s in %s" % (tok, rel))
    assert not leaks, "real PII present in repo: %s" % leaks


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
