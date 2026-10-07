"""Native PRIVATE storage regressions using only generated repositories."""
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import em_runtime as runtime
import em_watch
from private_storage_helpers import make_repository, make_linked_worktree, write_visibility, generator

FIX = generator.reliability_cases()
DATA = FIX['private_proof']


@pytest.fixture
def proof(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    if os.name == 'nt':
        import ctypes
        def profile_folder(window, folder, token, flags, buffer):
            buffer.value = str(tmp_path)
            return 0
        monkeypatch.setattr(ctypes.windll.shell32, 'SHGetFolderPathW', profile_folder)
    root = make_repository(tmp_path / 'companion')
    write_visibility(tmp_path)
    return root


def configure(root, text):
    with (root / '.git/config').open('a', encoding='utf-8') as handle:
        handle.write(text)


@pytest.mark.parametrize('linked', [False, True])
def test_fresh_private_receipt_proves_normal_and_linked_history(proof, linked):
    if linked:
        make_linked_worktree(proof, proof.parent / 'common')
    target = proof / 'data/state'
    answer = runtime.prove_private(target)
    assert answer['visibility'] == 'PRIVATE'
    assert answer['repositories'] == [DATA['slug']]
    assert not target.exists()


def test_companion_container_is_not_a_git_ignored_relative_dot(proof):
    (proof / '.gitignore').write_text('*.log\n\n# Synthetic private DATA\n!data/current.log\n', encoding='utf-8')
    assert runtime.prove_private(proof)['visibility'] == 'PRIVATE'
    assert runtime.prove_private(proof / 'data/current.log')['visibility'] == 'PRIVATE'
    with pytest.raises(ValueError):
        runtime.prove_private(proof / 'data/ignored.log')


@pytest.mark.parametrize('visibility', ['PUBLIC', 'UNKNOWN', 'malformed'])
def test_nonprivate_or_invalid_visibility_cannot_authorize_storage(proof, visibility):
    write_visibility(proof.parent, visibility=visibility)
    with pytest.raises(ValueError):
        runtime.prove_private(proof / 'data/state')


@pytest.mark.parametrize('stamp', ['', DATA['stale'], DATA['future'], 'invalid'])
def test_missing_stale_future_or_malformed_receipt_fails_closed(proof, stamp):
    write_visibility(proof.parent, stamp=stamp)
    with pytest.raises(ValueError):
        runtime.prove_private(proof / 'data/state')


def test_missing_visibility_receipt_does_not_trigger_network_lookup(proof):
    (proof.parent / '.pii-guard/visibility.json').unlink()
    with pytest.raises(ValueError):
        runtime.prove_private(proof / 'data/state')


def test_failed_git_discovery_cannot_fall_through_invalid_nested_marker(proof):
    (proof / 'data/.git').mkdir(parents=True)
    with pytest.raises(ValueError):
        runtime.prove_private(proof / 'data/state')


def test_source_destination_refused_before_git_or_visibility():
    with pytest.raises(ValueError):
        runtime.prove_private(runtime.SOURCE_ROOT / 'data/synthetic')


@pytest.mark.parametrize('rule', ['', *DATA['unsafe_ssh_rules'], *DATA['unsafe_transport_rules']])
def test_ssh_transport_policy_is_shared_and_never_executes_commands(proof, rule, monkeypatch):
    ssh = proof.parent / '.ssh/config'
    ssh.parent.mkdir()
    ssh.write_text('Host ' + DATA['alias'] + '\nHostName github.com\n' + rule + '\n', encoding='utf-8')
    system_ssh = proof.parent / 'ssh_config'
    system_ssh.write_text('', encoding='utf-8')
    # Isolate config discovery while retaining the real parser and PRIVATE proof.
    monkeypatch.setattr(runtime._storage_api(), '_ssh_config_sources', lambda: {
        'paths': [str(ssh), str(system_ssh)],
        'chains': [(str(ssh), str(system_ssh))],
    })
    (proof / '.git/config').write_text('[core]\nrepositoryformatversion = 0\nbare = false\n'
        '[remote "origin"]\nurl = git@' + DATA['alias'] + ':' + DATA['slug'] + '.git\n', encoding='utf-8')
    if rule:
        with pytest.raises(ValueError):
            runtime.prove_private(proof / 'data/state')
    else:
        assert runtime.prove_private(proof / 'data/state')['visibility'] == 'PRIVATE'


def test_https_host_is_not_resolved_as_an_ssh_alias(proof, monkeypatch):
    (proof / '.git/config').write_text('[remote "origin"]\nurl = ' + DATA['unrelated_https'], encoding='utf-8')
    real_read = Path.read_text
    def read(path, *args, **kwargs):
        if '.ssh' in path.parts:
            pytest.fail('HTTPS attempted to read SSH configuration')
        return real_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read)
    with pytest.raises(ValueError):
        runtime.prove_private(proof / 'data/state')


@pytest.mark.parametrize('config', [
    '\tpushurl = ' + DATA['public_remote'] + '\n',
    '[remote "backup"]\nurl = ' + DATA['public_remote'] + '\n',
    '[remote]\npushDefault = missing\n',
    '[branch "main"]\npushRemote = missing\n',
])
def test_all_effective_routes_and_selectors_must_be_private(proof, config):
    configure(proof, config)
    with pytest.raises(ValueError):
        runtime.prove_private(proof / 'data/state')


def test_inherited_repository_selector_cannot_authorize_public_physical_tree(proof, monkeypatch):
    public = make_repository(proof.parent / 'public')
    (public / '.git/config').write_text('[remote "origin"]\nurl = ' + DATA['public_remote'], encoding='utf-8')
    monkeypatch.setenv('GIT_DIR', str(proof / '.git'))
    monkeypatch.setenv('GIT_WORK_TREE', str(proof))
    with pytest.raises(ValueError):
        runtime.prove_private(public / 'data/state')
    assert runtime.prove_private(proof / 'data/state')['visibility'] == 'PRIVATE'


@pytest.mark.parametrize('failure', ['unborn', 'ignored'])
def test_private_data_requires_committed_history_and_unignored_destination(proof, failure):
    if failure == 'unborn':
        (proof / '.git/refs/heads/main').unlink()
    else:
        (proof / '.gitignore').write_text('data/\n', encoding='utf-8')
    with pytest.raises(ValueError):
        runtime.prove_private(proof / 'data/state')


def test_hardlinked_final_and_temporary_outputs_preserve_external_bytes(proof):
    outside = proof.parent / 'outside.txt'
    outside.write_text(DATA['retained_text'], encoding='utf-8')
    target = proof / 'state.json'
    os.link(outside, target)
    with pytest.raises(ValueError):
        em_watch.save_state(str(target), FIX['cursor'])
    target.unlink()
    os.link(outside, str(target) + '.tmp')
    em_watch.save_state(str(target), FIX['cursor'])
    assert outside.read_text(encoding='utf-8') == DATA['retained_text']
    assert json.loads(target.read_text(encoding='utf-8')) == FIX['cursor']


def test_relative_storage_retains_parent_alias_until_validation(proof):
    with pytest.raises(ValueError):
        runtime.storage_config({'storage': {'state_dir': '../companion/data'}}, proof)


def directory_alias(target, alias):
    if os.name == 'nt':
        import _winapi
        _winapi.CreateJunction(str(target), str(alias))
    else:
        alias.symlink_to(target, target_is_directory=True)


def test_directory_alias_is_rejected_before_resolving_to_another_private_repo(proof):
    other = make_repository(proof.parent / 'other-private')
    alias = proof / 'alias'
    directory_alias(other, alias)
    with pytest.raises(ValueError):
        runtime.prove_private(alias / 'state.json')
    assert not (other / 'state.json').exists()


def test_dangling_git_marker_cannot_fall_through_to_parent_private_repo(proof):
    metadata = proof.parent / 'missing-metadata'
    metadata.mkdir()
    nested = proof / 'nested'
    nested.mkdir()
    directory_alias(metadata, nested / '.git')
    metadata.rmdir()
    with pytest.raises(ValueError):
        runtime.prove_private(nested / 'state.json')
    assert not (nested / 'state.json').exists()


def test_deep_path_survives_repeated_atomic_writes(proof):
    target = proof.joinpath(*(['synthetic-segment'] * 15), 'state.json')
    em_watch.save_state(str(target), FIX['cursor'])
    em_watch.save_state(str(target), FIX['message'])
    assert json.loads(target.read_text(encoding='utf-8')) == FIX['message']


def test_atomic_write_rechecks_changed_publication_route(proof, monkeypatch):
    target = proof / 'state.json'
    target.write_text(DATA['retained_text'], encoding='utf-8')
    real_fsync = os.fsync
    def change_after_write(descriptor):
        real_fsync(descriptor)
        configure(proof, '\tpushurl = ' + DATA['public_remote'] + '\n')
    monkeypatch.setattr(os, 'fsync', change_after_write)
    with pytest.raises(ValueError):
        em_watch.save_state(str(target), FIX['cursor'])
    assert target.read_text(encoding='utf-8') == DATA['retained_text']
    assert not list(proof.glob('.state.json-*.tmp'))
