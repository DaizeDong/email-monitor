"""Coverage for scripts/init_config.py and scripts/verify_config.py.

These two scripts were found to have zero test coverage: a mutation probe that appends
`raise RuntimeError('SIE_MUTANT')` to the end of init_config.py left the whole suite green,
because nothing imports it. Looking closer turned up a second, worse gap: CONFIG.md documents
topic_labeling.enabled and the private rules/taxonomy.md, rules/sender_map.json and
rules/labels.json, and the generator wrote none of them, so a fresh machine following the docs
got a config directory missing everything the topic-labeling capability reads.

The contract test below is the one that matters: it runs the generator, points
EMAIL_MONITOR_CONFIG_DIR at what it produced, and asserts em_topic.load_config can actually
consume it. That is the seam where the generator and its consumer drifted apart before, and a
test that only imports init_config without running it end to end through the real loader would
not have caught it.
"""
import os
import sys

import pytest

TESTS_DIR = os.path.dirname(__file__)
SCRIPTS = os.path.abspath(os.path.join(TESTS_DIR, "..", "..", "..", "scripts"))
SKILL_SCRIPTS = os.path.abspath(os.path.join(TESTS_DIR, "..", "scripts"))
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, SKILL_SCRIPTS)

import init_config  # noqa: E402
import verify_config  # noqa: E402
import em_topic  # noqa: E402


@pytest.mark.parametrize("exists", [False])
def test_initializer_refuses_unversioned_storage_before_writing(tmp_path, monkeypatch, capsys, exists):
    out = tmp_path / "unversioned"
    if exists:
        out.mkdir()
    monkeypatch.setattr(sys, "argv", ["init_config.py", "--out", str(out)])
    assert init_config.main() == 1
    assert "Cannot initialize PRIVATE configuration" in capsys.readouterr().out
    assert out.exists() is exists
    assert not list(out.rglob("*"))


def test_fresh_uninitialized_config_is_not_runtime_ready(tmp_path, monkeypatch, capsys, private_companion):
    """A skeleton has no proven PRIVATE remote or selected-runtime measurement."""
    out = private_companion
    # Select the generated companion explicitly so discovery never probes a real sibling checkout.
    monkeypatch.setenv("EMAIL_MONITOR_CONFIG_DIR", str(out))
    argv = sys.argv
    sys.argv = ["init_config.py", "--out", str(out)]
    try:
        assert init_config.main() == 0
    finally:
        sys.argv = argv

    argv = sys.argv
    sys.argv = ["verify_config.py", "--config-dir", str(out)]
    try:
        exit_code = verify_config.main()
    finally:
        sys.argv = argv
    out_text = capsys.readouterr().out
    assert exit_code == 1, out_text
    assert "NOT READY" in out_text
    assert "PRIVATE" in out_text
