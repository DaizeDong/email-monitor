"""The per-process PRIVATE proof memo: one full proof per unchanged companion, refusals never kept.

Generated repositories only (see private_storage_helpers).
"""
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import em_runtime as runtime
import em_watch
from private_storage_helpers import make_repository, write_visibility, generator

DATA = generator.reliability_cases()['private_proof']


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
    boundary = runtime._storage_api()
    real = boundary.prove_private_companion
    calls = []

    def counted(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)
    monkeypatch.setattr(boundary, 'prove_private_companion', counted)
    return root, calls


def test_an_unchanged_companion_is_proved_in_full_once_per_process(proof):
    root, calls = proof
    for name in ('data/state/a.state.json', 'data/state/b.state.json', 'data/pool.db', 'data/log.txt'):
        assert runtime.prove_private(root / name)['visibility'] == 'PRIVATE'
    assert len(calls) == 1


def test_repeated_saves_reuse_the_proof(proof):
    root, calls = proof
    target = root / 'data' / 'state' / 'user1.state.json'
    for n in range(3):
        em_watch.save_state(str(target), {'n': n})
    assert json.loads(target.read_text(encoding='utf-8')) == {'n': 2}
    assert len(calls) == 1, 'every save re-ran the full proof (%d full proofs for 3 saves)' % len(calls)


def test_a_changed_publication_route_after_a_memoised_proof_is_refused(proof):
    root, calls = proof
    runtime.prove_private(root / 'data/state')
    with (root / '.git/config').open('a', encoding='utf-8') as handle:
        handle.write('\tpushurl = ' + DATA['public_remote'] + '\n')
    with pytest.raises(ValueError):
        runtime.prove_private(root / 'data/state')


def test_a_refusal_is_never_reused(proof, tmp_path):
    root, calls = proof
    write_visibility(tmp_path, visibility='PUBLIC')
    with pytest.raises(ValueError):
        runtime.prove_private(root / 'data/state')
    write_visibility(tmp_path)
    assert runtime.prove_private(root / 'data/state')['visibility'] == 'PRIVATE'
    assert len(calls) == 2


def test_an_expired_entry_is_proved_again(proof, monkeypatch):
    root, calls = proof
    now = [1000.0]
    monkeypatch.setattr(runtime, '_clock', lambda: now[0], raising=False)
    runtime.prove_private(root / 'data/state')
    now[0] += getattr(runtime, 'PROOF_TTL_SECONDS', 60.0) + 1
    runtime.prove_private(root / 'data/state')
    assert len(calls) == 2


def test_a_newly_ignored_destination_is_refused_from_the_memo(proof):
    root, calls = proof
    runtime.prove_private(root / 'data/state/a.json')
    (root / '.gitignore').write_text('data/state/b.json\n', encoding='utf-8')
    with pytest.raises(ValueError):
        runtime.prove_private(root / 'data/state/b.json')
    assert runtime.prove_private(root / 'data/state/a.json')['visibility'] == 'PRIVATE'


def test_ceiling_directories_disable_the_memo(proof, monkeypatch, tmp_path):
    root, calls = proof
    monkeypatch.setenv('GIT_CEILING_DIRECTORIES', str(tmp_path / 'elsewhere'))
    runtime.prove_private(root / 'data/state')
    runtime.prove_private(root / 'data/state')
    assert len(calls) == 2
