"""DATA path checks and atomic publication, using only generated repositories."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import em_runtime as runtime
import em_watch
from private_storage_helpers import make_repository, make_linked_worktree, generator

FIX = generator.reliability_cases()
DATA = FIX['private_proof']


@pytest.fixture
def companion(tmp_path):
    return make_repository(tmp_path / 'companion')


@pytest.mark.parametrize('linked', [False, True])
def test_normal_and_linked_worktrees_are_accepted_without_creating_data(companion, linked):
    if linked:
        make_linked_worktree(companion, companion.parent / 'common')
    target = companion / 'data/state'
    assert runtime.private_path(target) == target
    assert not target.exists()


def test_destination_outside_any_git_worktree_is_refused(tmp_path):
    with pytest.raises(ValueError, match='Git companion'):
        runtime.private_path(tmp_path / 'loose' / 'state.json')


def test_source_destination_refused():
    with pytest.raises(ValueError):
        runtime.private_path(runtime.SOURCE_ROOT / 'data/synthetic')


def test_path_checks_and_atomic_writes_start_no_process(companion, monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail('DATA path checks and atomic writes must not start a process')
    monkeypatch.setattr(subprocess, 'Popen', refuse)
    storage = runtime.storage_config({}, companion)
    assert storage['state_dir'] == str(companion / 'data' / 'state')
    em_watch.save_state(os.path.join(storage['state_dir'], 'user1.state.json'), FIX['cursor'])
    assert json.loads((companion / 'data/state/user1.state.json').read_text(encoding='utf-8')) == FIX['cursor']


def test_watch_cli_writes_literal_tilde_state_to_expanded_home(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    for name in ('HOME', 'USERPROFILE'):
        monkeypatch.setenv(name, str(home))
    make_repository(home / 'companion')
    cwd = tmp_path / 'cwd'
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(em_watch, 'run_once', lambda *a, **k: ([], {'uidvalidity': 1, 'last_uid': 0}))
    monkeypatch.setattr(sys, 'argv', ['em_watch.py', '--user', 'user1@example.com',
                                      '--state', '~/companion/data/state/user1.state.json'])
    assert em_watch.main() == 0
    assert (home / 'companion/data/state/user1.state.json').is_file()
    assert not (cwd / '~').exists()


def test_hardlinked_final_and_temporary_outputs_preserve_external_bytes(companion):
    outside = companion.parent / 'outside.txt'
    outside.write_text(DATA['retained_text'], encoding='utf-8')
    target = companion / 'state.json'
    os.link(outside, target)
    em_watch.save_state(str(target), FIX['cursor'])
    assert outside.read_text(encoding='utf-8') == DATA['retained_text']
    target.unlink()
    os.link(outside, str(target) + '.tmp')
    em_watch.save_state(str(target), FIX['cursor'])
    assert outside.read_text(encoding='utf-8') == DATA['retained_text']
    assert json.loads(target.read_text(encoding='utf-8')) == FIX['cursor']


def test_hardlinked_destination_is_refused_by_the_path_check(companion):
    outside = companion.parent / 'outside.txt'
    outside.write_text(DATA['retained_text'], encoding='utf-8')
    os.link(outside, companion / 'state.json')
    with pytest.raises(ValueError):
        runtime.private_path(companion / 'state.json')


def test_relative_storage_retains_parent_alias_until_validation(companion):
    with pytest.raises(ValueError):
        runtime.storage_config({'storage': {'state_dir': '../companion/data'}}, companion)


def directory_alias(target, alias):
    if os.name == 'nt':
        import _winapi
        _winapi.CreateJunction(str(target), str(alias))
    else:
        alias.symlink_to(target, target_is_directory=True)


def test_directory_alias_is_rejected_before_resolving_to_another_repo(companion):
    other = make_repository(companion.parent / 'other-private')
    alias = companion / 'alias'
    directory_alias(other, alias)
    with pytest.raises(ValueError):
        runtime.private_path(alias / 'state.json')
    assert not (other / 'state.json').exists()


def test_deep_path_survives_repeated_atomic_writes(companion):
    target = companion.joinpath(*(['synthetic-segment'] * 15), 'state.json')
    em_watch.save_state(str(target), FIX['cursor'])
    em_watch.save_state(str(target), FIX['message'])
    assert json.loads(target.read_text(encoding='utf-8')) == FIX['message']


def test_failed_atomic_write_keeps_the_old_file_and_no_temporary(companion, monkeypatch):
    target = companion / 'state.json'
    target.write_text(DATA['retained_text'], encoding='utf-8')
    def fail(descriptor):
        raise OSError('synthetic fsync failure')
    monkeypatch.setattr(os, 'fsync', fail)
    with pytest.raises(OSError):
        em_watch.save_state(str(target), FIX['cursor'])
    assert target.read_text(encoding='utf-8') == DATA['retained_text']
    assert not list(companion.glob('.state.json-*.tmp'))


def test_same_interpreter_probe_imports_in_process(monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail('probing this interpreter must not start another one')
    monkeypatch.setattr(subprocess, 'run', refuse)
    assert runtime.probe_interpreter(sys.executable) == (True, 'interpreter and llmcall import verified')
