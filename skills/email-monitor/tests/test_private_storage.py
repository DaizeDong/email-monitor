"""DATA path checks and atomic publication, using only generated repositories."""
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import em_runtime as runtime
import em_watch
from private_storage_helpers import make_repository, generator

FIX = generator.reliability_cases()
DATA = FIX['private_proof']


@pytest.fixture
def companion(tmp_path):
    return make_repository(tmp_path / 'companion')


def test_destination_outside_any_git_worktree_is_refused(tmp_path):
    with pytest.raises(ValueError, match='Git companion'):
        runtime.private_path(tmp_path / 'loose' / 'state.json')


def test_source_destination_refused():
    with pytest.raises(ValueError):
        runtime.private_path(runtime.SOURCE_ROOT / 'data/synthetic')


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
