#!/usr/bin/env python3
"""Guard: no real personal PII anywhere in this PUBLIC repo -- tree AND history.

WHAT THIS FILE USED TO BE
-------------------------
Until 2026-07-13 this guard carried a hardcoded `PII_DENYLIST` of eleven of the operator's real
identifiers -- employer, residence, three Gmail slugs, phone (two spellings), street, city -- each
split into fragments (`"str" + "eet"`) and labeled with a comment saying exactly what it was.
The stated reason for the fragments was so that the guard "would not self-match".

That is the same mistake its sibling in demand-mining made, and it is worth naming plainly: the
author saw that the values would trip the scanner and chose to hide them from it, rather than
concluding that a public repo is no place for the values. A split-fragment dossier is still a
dossier -- `"str" + "eet"` reassembles at import. The guard against the leak WAS the leak.

    A DENYLIST OF REAL IDENTIFIERS *IS* A PII DOCUMENT.
    A hardcoded one is also useless as a guard: it can only match what its author already thought
    of, and the actual 2026-07 leak was a vendor nobody had listed.

WHAT IT IS NOW
--------------
It delegates to `tools/pii_guard.py`, which is the inverse design:
  * an ALLOWLIST -- anything real-world-shaped outside the declared synthetic namespace is a
    finding, including identifiers nobody predicted; it needs no private data, so it runs in CI.
  * an optional private denylist read at RUNTIME from `~/.pii-denylist.json`, OUTSIDE every repo,
    never committed -- the operator's own sensitive words live in exactly one place, off in the
    dark, and this file holds none of them.

There are no needles here. There cannot be. That is the whole point.
"""
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))  # skills/email-monitor/tests -> root
GUARD = os.path.join(REPO_ROOT, "tools", "pii_guard.py")


def test_pii_guard_is_vendored():
    assert os.path.isfile(GUARD), (
        "tools/pii_guard.py is missing; re-vendor it from the shared pii-guard master."
    )


def test_no_real_pii_in_tree_or_history():
    """Tree AND history. Once PII is in a commit, editing the file is not a fix -- the commit is
    still on GitHub. That is how every leak in the 2026-07 audit survived being 'fixed'."""
    if not os.path.isfile(GUARD):
        pytest.skip("pii_guard not vendored")
    p = subprocess.run([sys.executable, GUARD, "--tree", "--history"],
                       cwd=REPO_ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert p.returncode == 0, "pii_guard found real private data:\n" + (p.stdout or "") + (p.stderr or "")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
