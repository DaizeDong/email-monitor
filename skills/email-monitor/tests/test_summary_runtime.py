"""Selected interpreter reaches summary adapters; inputs come from generated cases."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import em_summary as summary


def test_summary_rejects_unversioned_registry_before_read(tmp_path, monkeypatch):
    registry = tmp_path/'registry.json'
    registry.write_text('{}')
    monkeypatch.setattr(summary.json, 'load', lambda *a, **k: pytest.fail('private registry read'))
    monkeypatch.setattr(sys, 'argv', ['em_summary.py', '--config', str(registry)])
    assert summary.main() == 1
