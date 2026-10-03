#!/usr/bin/env python3
"""Run the shared structural PII guard against the public tree and Git history.

Public tests must not embed personal identifiers, including fragmented ones.
The scanner uses its public synthetic namespace and can load a separate private
denylist at runtime. Offline tests redirect the profile and do not read that list.
Passing this scan establishes only the implemented scanner checks; it does not
prove that every private narrative is absent.
"""
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))  # skills/email-monitor/tests -> root
GUARD = os.path.join(REPO_ROOT, "guards", "tools", "pii_guard.py")


def test_pii_guard_is_vendored():
    assert os.path.isfile(GUARD), (
        "guards/tools/pii_guard.py is missing, so nothing scanned this repo. "
        "The guards submodule is not checked out: run `git submodule update --init`. Do not re-vendor a copy."
    )


def test_no_real_pii_in_tree_or_history():
    """Scan tree and history: changing a file cannot remove an earlier committed value."""
    if not os.path.isfile(GUARD):
        pytest.skip("pii_guard not vendored")
    p = subprocess.run([sys.executable, GUARD, "--tree", "--history"],
                       cwd=REPO_ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert p.returncode == 0, "pii_guard found real private data:\n" + (p.stdout or "") + (p.stderr or "")


def test_scanner_profile_is_limited_to_original_read_only_commands(request):
    # Kit conftest modules share this import name during repository-root collection.
    conftest = request.config.pluginmanager.get_plugin(os.path.join(REPO_ROOT, "conftest.py"))
    assert conftest is not None

    for case in conftest._generator.scanner_command_cases():
        command = [sys.executable, GUARD, *case["arguments"]]
        assert conftest._is_guard_scan(command) is case["allowed"]
    assert os.environ["USERPROFILE"] == str(conftest._SANDBOX)
    assert os.environ["HOME"] == str(conftest._SANDBOX)
    assert "GMAIL_APP_PW" not in os.environ


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
