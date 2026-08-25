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
import json
import os
import subprocess
import sys

TESTS_DIR = os.path.dirname(__file__)
SCRIPTS = os.path.abspath(os.path.join(TESTS_DIR, "..", "..", "..", "scripts"))
SKILL_SCRIPTS = os.path.abspath(os.path.join(TESTS_DIR, "..", "scripts"))
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, SKILL_SCRIPTS)

import init_config  # noqa: E402
import verify_config  # noqa: E402
import em_topic  # noqa: E402


def test_registry_declares_topic_labeling_disabled_by_default():
    """An uninitialised machine must stay inert (CONFIG.md, em_tick.py default)."""
    assert init_config.REGISTRY["topic_labeling"]["enabled"] is False


def test_generator_output_is_consumable_by_em_topic_load_config(tmp_path, monkeypatch):
    """The contract test. Before the fix, init_config.py never wrote rules/taxonomy.md,
    rules/sender_map.json or rules/labels.json at all, so this would fail with
    load_config(...) staying None even after the operator filled in an account -- the files
    it needed to edit did not exist. After the fix, the generator's own skeleton is exactly
    what the loader expects."""
    out = tmp_path / "cfg"
    # Invoke exactly the way an operator does: as a script with --out.
    argv = sys.argv
    sys.argv = ["init_config.py", "--out", str(out)]
    try:
        exit_code = init_config.main()
    finally:
        sys.argv = argv
    assert exit_code == 0

    tax = out / "rules" / "taxonomy.md"
    smap = out / "rules" / "sender_map.json"
    labels = out / "rules" / "labels.json"
    assert tax.is_file()
    assert smap.is_file()
    assert labels.is_file()

    # Fresh out of the generator, nothing is configured yet: load_config must stay inert
    # rather than raise, exactly like the "not initialised" case in test_topic_config.py.
    monkeypatch.setenv("EMAIL_MONITOR_CONFIG_DIR", str(out))
    assert em_topic.load_config("primary") is None

    # Now do what CONFIG.md tells an operator to do: fill in the per-account label set the
    # generator left empty. Everything else (taxonomy.md, sender_map.json's shape) is used
    # as the generator produced it, unedited.
    labels_data = json.loads(labels.read_text(encoding="utf-8"))
    assert labels_data == {}
    labels_data["primary"] = ["Payments", "Scheduling"]
    labels.write_text(json.dumps(labels_data), encoding="utf-8")

    cfg = em_topic.load_config("primary")
    assert cfg is not None
    assert cfg["allowed_labels"] == ["Payments", "Scheduling"]
    assert cfg["sender_map"] == {"version": 1, "by_address": {}, "by_domain": {}, "by_list_id": {}}
    assert "taxonomy" in cfg and cfg["taxonomy"]


def test_write_does_not_clobber_without_force(tmp_path):
    p = tmp_path / "a" / "f.txt"
    init_config.write(str(p), "first\n", force=False)
    init_config.write(str(p), "second\n", force=False)
    assert p.read_text(encoding="utf-8") == "first\n"


def test_write_clobbers_with_force(tmp_path):
    p = tmp_path / "a" / "f.txt"
    init_config.write(str(p), "first\n", force=False)
    init_config.write(str(p), "second\n", force=True)
    assert p.read_text(encoding="utf-8") == "second\n"


def test_running_generator_twice_is_idempotent_and_does_not_corrupt(tmp_path):
    out = tmp_path / "cfg"
    argv = sys.argv
    sys.argv = ["init_config.py", "--out", str(out)]
    try:
        assert init_config.main() == 0
        registry_after_first = (out / "registry.json").read_text(encoding="utf-8")
        assert init_config.main() == 0
    finally:
        sys.argv = argv
    registry_after_second = (out / "registry.json").read_text(encoding="utf-8")
    assert registry_after_first == registry_after_second
    # No force on the second run: nothing an operator may have started editing was touched.
    assert json.loads(registry_after_second) == init_config.REGISTRY


def test_verify_config_passes_on_a_freshly_generated_config(tmp_path, capsys):
    """A generator whose own output cannot pass the doctor it ships alongside would be a
    second, independent drift between the two scripts -- exercise that seam too."""
    out = tmp_path / "cfg"
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
    assert exit_code == 0, out_text
    assert "READY" in out_text


def test_verify_config_reports_missing_config_dir(tmp_path, capsys):
    missing = tmp_path / "does-not-exist"
    argv = sys.argv
    sys.argv = ["verify_config.py", "--config-dir", str(missing)]
    try:
        exit_code = verify_config.main()
    finally:
        sys.argv = argv
    out_text = capsys.readouterr().out
    assert exit_code == 1
    assert "FAIL" in out_text
