"""All entrypoints agree on explicit and sibling companion discovery."""
import importlib.util
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest

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


def test_sibling_resolver_is_shared_by_tick_and_doctor(tmp_path, monkeypatch):
    selected = tmp_path / CASES['companion']
    selected.mkdir()
    monkeypatch.delenv('EMAIL_MONITOR_CONFIG', raising=False)
    monkeypatch.delenv('EMAIL_MONITOR_CONFIG_DIR', raising=False)
    monkeypatch.setattr(em_runtime, '_resolved_companion_root', lambda: str(selected))
    assert em_tick.resolve_config(None) == str(selected / 'registry.json')
    assert verify_config.discover(None)[0] == str(selected)


def test_explicit_registry_wins_over_environment(tmp_path, monkeypatch):
    selected = tmp_path / CASES['explicit']
    monkeypatch.setenv('EMAIL_MONITOR_CONFIG', str(tmp_path / CASES['missing']))
    assert em_tick.resolve_config(str(selected / 'registry.json')) == str(selected / 'registry.json')
    assert verify_config.discover(str(selected))[0] == str(selected)


def pinned_layout(tmp_path, monkeypatch, *, companion=True, legacy=False):
    profile = offline_profile._SANDBOX / tmp_path.name
    source, selected = generator.make_companion_discovery_layout(
        profile, companion=companion, legacy=legacy)
    tools = source / 'guards/tools'
    tools.mkdir(parents=True)
    shutil.copyfile(ROOT / 'guards/tools/datadir.py', tools / 'datadir.py')
    monkeypatch.setattr(em_runtime, 'SOURCE_ROOT', source)
    monkeypatch.delenv('EMAIL_MONITOR_CONFIG', raising=False)
    monkeypatch.delenv('EMAIL_MONITOR_CONFIG_DIR', raising=False)
    monkeypatch.setenv('EMAIL_MONITOR_DATA_DIR', str(source.parent / CASES['data_override']))
    monkeypatch.setenv('HOME', str(source.parent / 'profile'))
    monkeypatch.setenv('USERPROFILE', str(source.parent / 'profile'))
    monkeypatch.delitem(sys.modules, 'datadir', raising=False)
    return source, selected


def test_real_pinned_resolver_returns_sibling_root_before_legacy_or_data_override(tmp_path, monkeypatch):
    source, selected = pinned_layout(tmp_path, monkeypatch, legacy=True)
    # The installed resolver derives its source owner from its own physical path.
    assert em_runtime.companion_dir() == selected
    assert em_tick.resolve_config(None) == str(selected / 'registry.json')
    assert verify_config.discover(None)[0] == str(selected)
    assert init_config.default_dir() == str(selected)


def test_foreign_cached_datadir_cannot_choose_the_companion(tmp_path, monkeypatch):
    source, selected = pinned_layout(tmp_path, monkeypatch)
    monkeypatch.setitem(sys.modules, 'datadir', SimpleNamespace(
        resolve_companion_root=lambda _: pytest.fail('foreign resolver was used')))
    assert em_runtime.companion_dir() == selected


def test_missing_guards_fails_discovery_before_selecting_fallback(tmp_path, monkeypatch):
    source, selected = generator.make_companion_discovery_layout(
        offline_profile._SANDBOX / tmp_path.name)
    monkeypatch.setattr(em_runtime, 'SOURCE_ROOT', source)
    monkeypatch.delenv('EMAIL_MONITOR_CONFIG', raising=False)
    monkeypatch.delenv('EMAIL_MONITOR_CONFIG_DIR', raising=False)
    with pytest.raises(ValueError, match='Initialize the pinned Guards'):
        em_runtime.companion_dir()
    with pytest.raises(ValueError, match='Initialize the pinned Guards'):
        init_config.default_dir()


def test_explicit_missing_selection_wins_with_guards_absent(tmp_path, monkeypatch):
    source, selected = generator.make_companion_discovery_layout(
        offline_profile._SANDBOX / tmp_path.name)
    monkeypatch.setattr(em_runtime, 'SOURCE_ROOT', source)
    missing = tmp_path / CASES['missing']
    monkeypatch.setenv('EMAIL_MONITOR_CONFIG', str(missing))
    assert init_config.default_dir() == str(missing)
    assert em_tick.resolve_config(None) == str(missing / 'registry.json')


def test_initializer_selects_source_sibling_when_no_companion_exists(tmp_path, monkeypatch):
    source, selected = pinned_layout(tmp_path, monkeypatch, companion=False)
    monkeypatch.setattr(init_config, 'DEFAULT_DIR', str(selected))
    assert em_runtime.companion_dir() is None
    assert init_config.default_dir() == str(selected)
    assert not selected.exists()


def test_quality_review_without_companion_is_inert_and_force_is_explicit(tmp_path, monkeypatch, capsys):
    pinned_layout(tmp_path, monkeypatch, companion=False)
    arguments = ['--account', CASES['account'], '--user', CASES['user']]
    assert em_quality_review.main(arguments) == 0
    assert 'nothing was reviewed' in capsys.readouterr().out
    with pytest.raises(SystemExit) as result:
        em_quality_review.main(arguments + ['--force'])
    assert result.value.code == 2


def test_backfill_reads_generated_rules_from_sibling_root(tmp_path, monkeypatch):
    source, selected = pinned_layout(tmp_path, monkeypatch)
    account, credential = generator.make_backfill_discovery_rules(selected)
    monkeypatch.setenv('GMAIL_APP_PW', credential)
    monkeypatch.setattr(em_backfill, 'run_tool', lambda *a, **k: (0, True))
    assert em_backfill.main(['--account', account['slug'], '--user', account['user']]) == 0


def test_backfill_explicit_resolver_environment_does_not_discover_on_import(tmp_path, monkeypatch):
    selected = str(tmp_path / 'resolve-cred.ps1')
    monkeypatch.setenv('EMAIL_MONITOR_RESOLVE_CRED', selected)
    monkeypatch.setattr(em_runtime, 'companion_file', lambda *a:
                        pytest.fail('implicit companion discovery reached'))
    spec = importlib.util.spec_from_file_location('synthetic_backfill_import',
        ROOT / 'skills/email-monitor/scripts/em_backfill.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.RESOLVE_CRED == selected


def test_quality_explicit_missing_registry_does_not_discover(tmp_path, monkeypatch):
    monkeypatch.setattr(em_runtime, 'companion_file', lambda *a:
                        pytest.fail('implicit companion discovery reached'))
    arguments = ['--account', CASES['account'], '--user', CASES['user'],
                 '--registry', str(tmp_path / 'missing.json')]
    assert em_quality_review.main(arguments) == 0
