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
import copy
import json
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


def test_registry_declares_topic_labeling_disabled_by_default():
    """An uninitialised machine must stay inert (CONFIG.md, em_tick.py default)."""
    assert init_config.REGISTRY["topic_labeling"]["enabled"] is False


@pytest.mark.parametrize("exists", [False, True])
def test_initializer_refuses_unversioned_storage_before_writing(tmp_path, monkeypatch, capsys, exists):
    out = tmp_path / "unversioned"
    if exists:
        out.mkdir()
    monkeypatch.setattr(sys, "argv", ["init_config.py", "--out", str(out)])
    assert init_config.main() == 1
    assert "Cannot initialize PRIVATE configuration" in capsys.readouterr().out
    assert out.exists() is exists
    assert not list(out.rglob("*"))


def test_generator_output_is_consumable_by_em_topic_load_config(tmp_path, monkeypatch, private_companion):
    """The contract test. Before the fix, init_config.py never wrote rules/taxonomy.md,
    rules/sender_map.json or rules/labels.json at all, so this would fail with
    load_config(...) staying None even after the operator filled in an account -- the files
    it needed to edit did not exist. After the fix, the generator's own skeleton is exactly
    what the loader expects."""
    out = private_companion
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
    monkeypatch.delenv("EMAIL_MONITOR_CONFIG", raising=False)
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


def test_write_does_not_clobber_without_force(tmp_path, private_companion):
    p = private_companion / "rules/kill_list.txt"
    init_config.write(str(p), "first\n", force=False, root=private_companion)
    init_config.write(str(p), "second\n", force=False, root=private_companion)
    assert p.read_text(encoding="utf-8") == "first\n"


def test_write_clobbers_with_force(tmp_path, private_companion):
    p = private_companion / "rules/kill_list.txt"
    init_config.write(str(p), "first\n", force=False, root=private_companion)
    init_config.write(str(p), "second\n", force=True, root=private_companion)
    assert p.read_text(encoding="utf-8") == "second\n"


@pytest.mark.parametrize(
    ("hardlinked", "force", "expected_exit"),
    [(True, True, 1), (True, False, 1), (False, True, 0), (False, False, 0)],
    ids=["hardlink-force", "hardlink-preserve", "regular-force", "regular-preserve"],
)
def test_initializer_preserves_external_hardlink_content(
    tmp_path, monkeypatch, hardlinked, force, expected_exit, private_companion
):
    with open(os.path.join(TESTS_DIR, "drafting.json"), encoding="utf-8") as handle:
        fixture = json.load(handle)
    registry = copy.deepcopy(init_config.REGISTRY)
    registry["draft"] = fixture["alternate_config"]
    original = (json.dumps(registry, indent=2) + "\n").encode("utf-8")
    outside = tmp_path / "outside.json"
    outside.write_bytes(original)
    out = private_companion
    target = out / "registry.json"
    if hardlinked:
        os.link(outside, target)
        assert target.stat().st_nlink == 2
    else:
        target.write_bytes(original)
    outside_before = outside.stat()
    monkeypatch.setattr(
        sys, "argv", ["init_config.py", "--out", str(out)] + (["--force"] if force else [])
    )

    exit_code = init_config.main()

    assert outside.read_bytes() == original
    outside_after = outside.stat()
    assert (outside_after.st_dev, outside_after.st_ino) == (
        outside_before.st_dev, outside_before.st_ino
    )
    assert exit_code == expected_exit
    if hardlinked:
        assert os.path.samefile(outside, target)
        assert target.stat().st_nlink == 2
    if hardlinked and force:
        assert not (out / "rules").exists()
    elif force:
        assert json.loads(target.read_text(encoding="utf-8")) == init_config.REGISTRY
    else:
        assert target.read_bytes() == original


def test_running_generator_twice_is_idempotent_and_does_not_corrupt(tmp_path, private_companion):
    out = private_companion
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


def test_fresh_uninitialized_config_is_not_runtime_ready(tmp_path, capsys, private_companion):
    """A skeleton has no proven PRIVATE remote or selected-runtime measurement."""
    out = private_companion
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
