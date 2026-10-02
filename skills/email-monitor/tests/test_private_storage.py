"""PRIVATE proof must use current GitHub visibility and scoped Git discovery."""
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import em_runtime as runtime

FIX = json.loads(Path(__file__).with_name('reliability.json').read_text(encoding='utf-8'))
DATA = FIX['private_proof']


@pytest.fixture
def proof(tmp_path, monkeypatch):
    root = tmp_path/'companion'
    (root/'.git').mkdir(parents=True)
    (root/'.git/config').write_text('[remote "origin"]\nurl = '+FIX['private_remote'], encoding='utf-8')
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    cache = tmp_path/'.pii-guard/visibility.json'
    calls = []
    state = {'visibility': 'PRIVATE', 'git_ok': True, 'remote': FIX['private_remote']}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromisoformat(DATA['now'])

    monkeypatch.setattr(runtime, 'datetime', Clock, raising=False)

    def query(argv, **kwargs):
        calls.append(argv)
        if argv[0] == 'git':
            if not state['git_ok']:
                return SimpleNamespace(returncode=128, stdout='', stderr='not a Git worktree')
            if argv[-2:] == ['rev-parse', '--show-toplevel']:
                return SimpleNamespace(returncode=0, stdout=str(root), stderr='')
            if argv[-3:] in (['remote', 'get-url', 'origin'], ['config', '--get', 'remote.origin.url']):
                return SimpleNamespace(returncode=0, stdout=state['remote'], stderr='')
        if argv[:3] == ['gh', 'repo', 'view'] and argv[3] == DATA['slug']:
            value = state['visibility']
            if value == 'unavailable':
                return SimpleNamespace(returncode=1, stdout='', stderr='synthetic offline')
            if value == 'malformed':
                return SimpleNamespace(returncode=0, stdout='invalid JSON', stderr='')
            return SimpleNamespace(returncode=0, stdout=json.dumps({'nameWithOwner': DATA['slug'], 'visibility': value}), stderr='')
        pytest.fail('Unadmitted PRIVATE verification process: '+repr(argv))

    monkeypatch.setattr(runtime, '_run', query, raising=False)
    monkeypatch.setattr(subprocess, 'run', query)

    def write_cache(stamp, value='PRIVATE'):
        cache.parent.mkdir(exist_ok=True)
        cache.write_text(json.dumps({'_refreshed':stamp, DATA['slug']:value}), encoding='utf-8')

    return SimpleNamespace(root=root, target=root/'data/state', cache=cache,
                           calls=calls, state=state, write_cache=write_cache)


@pytest.mark.parametrize('linked', [False, True])
def test_live_private_proof_does_not_require_machine_cache(proof, linked):
    if linked:
        (proof.root/'.git/config').unlink()
        (proof.root/'.git').rmdir()
        (proof.root/'.git').write_text('gitdir: ../metadata/worktrees/synthetic', encoding='utf-8')
    answer = runtime.prove_private(proof.target)
    assert answer['visibility'] == 'PRIVATE'
    assert answer['repository'] == DATA['slug']
    assert not proof.cache.exists() and not proof.target.exists()


@pytest.mark.parametrize('visibility', ['PUBLIC', 'UNKNOWN', 'malformed'])
def test_live_nonprivate_or_invalid_answer_cannot_be_overruled_by_cache(proof, visibility):
    proof.state['visibility'] = visibility
    proof.write_cache(DATA['fresh'])
    with pytest.raises(ValueError):
        runtime.prove_private(proof.target)
    assert not proof.target.exists()


@pytest.mark.parametrize('stamp', [None, DATA['stale'], DATA['future'], 'invalid'])
def test_unavailable_live_lookup_rejects_untrusted_cache(proof, stamp):
    proof.state['visibility'] = 'unavailable'
    proof.write_cache(stamp)
    with pytest.raises(ValueError):
        runtime.prove_private(proof.target)
    assert not proof.target.exists()


def test_bounded_fresh_private_cache_supports_offline_readiness(proof):
    proof.state['visibility'] = 'unavailable'
    proof.write_cache(DATA['fresh'])
    assert runtime.prove_private(proof.target)['visibility'] == 'PRIVATE'
    assert not proof.target.exists()


def test_failed_git_discovery_prevents_origin_and_visibility_reads(proof):
    proof.state['git_ok'] = False
    proof.write_cache(DATA['fresh'])
    with pytest.raises(ValueError):
        runtime.prove_private(proof.target)
    assert not any(argv[0] == 'gh' or 'origin' in argv for argv in proof.calls)


def test_source_destination_refused_before_git_or_visibility(proof):
    with pytest.raises(ValueError):
        runtime.prove_private(runtime.SOURCE_ROOT/'data/synthetic')
    assert proof.calls == []


def test_configured_ssh_alias_resolves_without_executing_ssh(proof):
    ssh = proof.root.parent/'.ssh'
    ssh.mkdir()
    (ssh/'config').write_text(DATA['ssh_config'], encoding='utf-8')
    proof.state['remote'] = 'git@'+DATA['alias']+':'+DATA['slug']+'.git'
    assert runtime.prove_private(proof.target)['visibility'] == 'PRIVATE'
    assert all(argv[0] != 'ssh' for argv in proof.calls)


@pytest.mark.parametrize('rule', DATA['unsafe_ssh_rules'])
def test_dynamic_ssh_config_cannot_authorize_private_storage(proof, rule):
    ssh = proof.root.parent/'.ssh'
    ssh.mkdir()
    (ssh/'config').write_text(DATA['ssh_config']+rule+'\n', encoding='utf-8')
    proof.state['remote'] = 'git@'+DATA['alias']+':'+DATA['slug']+'.git'
    with pytest.raises(ValueError):
        runtime.prove_private(proof.target)
    assert all(argv[0] != 'gh' for argv in proof.calls)


def test_https_host_is_not_resolved_as_an_ssh_alias(proof, monkeypatch):
    proof.state['remote'] = DATA['unrelated_https']
    real_read = Path.read_text

    def read(path, *args, **kwargs):
        if '.ssh' in path.parts:
            pytest.fail('HTTPS attempted to read SSH configuration')
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', read)
    with pytest.raises(ValueError):
        runtime.prove_private(proof.target)
    assert all(argv[0] != 'gh' for argv in proof.calls)
