"""Standalone CLI boundaries using only generator-produced synthetic mail and config."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from private_storage_helpers import make_repository

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'skills/email-monitor/scripts'))
import em_filters as filters
import em_quality_review as review

FIX = json.loads((Path(__file__).parent/'reliability.json').read_text(encoding='utf-8'))
spec = importlib.util.spec_from_file_location('standalone_cli_cases', ROOT/'tools/make_fixtures.py')
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)
DOCTOR = generator.doctor_cases()
RESPONSES = generator.quality_review_response_cases(
    FIX['model_policy']['taxonomy'], FIX['model_policy']['topic_config']['allowed_labels'][0])


@pytest.fixture
def private_cli(tmp_path, monkeypatch):
    companion = tmp_path/'companion'
    make_repository(companion)
    (companion/'rules').mkdir()
    registry = {'accounts': [copy.deepcopy(FIX['account'])],
                'runtime': {'python': sys.executable, 'local_only': False},
                'quality_review': {'enabled': True}}
    (companion/'registry.json').write_text(json.dumps(registry))
    (companion/'rules/taxonomy.md').write_text(FIX['model_policy']['taxonomy'])
    (companion/'rules/sender_map.json').write_text(json.dumps(DOCTOR['sender_map']))
    (companion/'rules/labels.json').write_text(json.dumps(
        {FIX['account']['slug']: FIX['model_policy']['topic_config']['allowed_labels']}))
    monkeypatch.setenv('EMAIL_MONITOR_CONFIG_DIR', str(companion))
    monkeypatch.setenv('GMAIL_APP_PW', FIX['message']['message_id'])
    calls = []
    monkeypatch.setattr(review, 'fetch_labelled', lambda *a, **k:
                        [(FIX['message']['from'], FIX['message']['subject'])])
    def judge(*args, **kwargs):
        calls.append(args)
        return []
    monkeypatch.setattr(review, 'judge', judge)
    def quality(*extra):
        return review.main(['--account', FIX['account']['slug'], '--user', FIX['account']['user'],
                            '--registry', str(companion/'registry.json'), *map(str, extra)])
    def compile(*extra):
        monkeypatch.setattr(sys, 'argv', ['em_filters.py', '--sender-map',
                                       str(companion/'rules/sender_map.json'), *map(str, extra)])
        return filters.main()
    return SimpleNamespace(root=companion, registry=registry, quality=quality, compile=compile, calls=calls)


def no_topic_read(monkeypatch):
    monkeypatch.setattr(review.em_topic, 'load_config', lambda *a, **k: pytest.fail('topic DATA read'))


def test_quality_output_rejected_before_topic_or_mail(private_cli, tmp_path, monkeypatch):
    outside = tmp_path/'unversioned'
    outside.mkdir()
    no_topic_read(monkeypatch)
    assert private_cli.quality('--json', outside/'report.json', '--force') == 1
    assert not (outside/'report.json').exists()


def test_filter_output_rejected_before_sender_map_read(private_cli, tmp_path, monkeypatch):
    outside = tmp_path/'unversioned'
    outside.mkdir()
    monkeypatch.setattr(filters.json, 'load', lambda *a, **k: pytest.fail('sender map read'))
    assert private_cli.compile('--out', outside/'filters.xml') == 1
    assert not (outside/'filters.xml').exists()
