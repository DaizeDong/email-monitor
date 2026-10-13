"""All entrypoints agree on explicit and sibling companion discovery."""
import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/email-monitor/scripts'))
import conftest as offline_profile
import em_runtime
import em_tick
import em_quality_review
import em_backfill
import init_config
import verify_config
spec = importlib.util.spec_from_file_location('layout_fixture_generator', ROOT / 'tools/make_fixtures.py')
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)
CASES = generator.companion_discovery_cases()


def test_missing_explicit_override_does_not_select_another_companion(tmp_path, monkeypatch):
    selected = tmp_path / CASES['missing']
    monkeypatch.setenv('EMAIL_MONITOR_CONFIG', str(selected))
    assert em_runtime.companion_dir() == selected.absolute()
    assert em_tick.resolve_config(None) == str(selected / 'registry.json')
    assert verify_config.discover(None)[0] == str(selected)
