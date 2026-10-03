"""Standalone CLI boundaries using only generator-produced synthetic mail and config."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import xml.etree.ElementTree as ET

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


def test_quality_registry_rejected_before_read(private_cli, tmp_path, monkeypatch):
    registry = tmp_path/'registry.json'
    registry.write_text(json.dumps(private_cli.registry))
    no_topic_read(monkeypatch)
    assert private_cli.quality('--registry', registry, '--force') == 1


@pytest.mark.parametrize('section', ['classifier', 'topic_labeling', 'quality_review'])
def test_quality_obsolete_policy_rejected_before_topic(private_cli, monkeypatch, section):
    private_cli.registry.setdefault(section, {})['timeout'] = 17
    (private_cli.root/'registry.json').write_text(json.dumps(private_cli.registry))
    no_topic_read(monkeypatch)
    assert private_cli.quality('--force') == 1


def test_quality_local_only_rejected_before_topic(private_cli, monkeypatch):
    private_cli.registry['runtime']['local_only'] = True
    (private_cli.root/'registry.json').write_text(json.dumps(private_cli.registry))
    no_topic_read(monkeypatch)
    assert private_cli.quality('--force') == 1


def test_quality_missing_model_dependency_precedes_mail(private_cli, monkeypatch):
    monkeypatch.setattr(review, 'llmcall', None)
    monkeypatch.setattr(review, 'fetch_labelled', lambda *a, **k: pytest.fail('mail reached'))
    assert private_cli.quality() == 5


def test_quality_success_writes_private_report_atomically(private_cli):
    report = private_cli.root/'reports/quality.json'
    assert private_cli.quality('--json', report) == 0
    saved = json.loads(report.read_text())
    assert saved['findings'] == [] and saved['unreviewed'] == []
    assert saved['sampled'] > 0
    assert not report.with_suffix('.json.tmp').exists()
    assert private_cli.calls


def test_quality_missing_registry_stays_inert_without_force(tmp_path, capsys):
    assert review.main(['--account', FIX['account']['slug'], '--user', FIX['account']['user'],
                        '--registry', str(tmp_path/'missing.json')]) == 0
    assert 'DISABLED' in capsys.readouterr().out


def test_quality_failed_fetch_never_reports_clean(private_cli, monkeypatch, capsys):
    monkeypatch.setattr(review, 'fetch_labelled', lambda *a, **k: None)
    report = private_cli.root/'quality.json'
    assert private_cli.quality('--json', report) == 5
    assert not private_cli.calls
    assert 'no findings.' not in capsys.readouterr().out
    assert all(row['reason'] == 'fetch_failed' for row in json.loads(report.read_text())['unreviewed'])


def test_quality_empty_fetch_is_explicit(private_cli, monkeypatch, capsys):
    monkeypatch.setattr(review, 'fetch_labelled', lambda *a, **k: [])
    assert private_cli.quality() == 0
    assert 'nothing reviewed' in capsys.readouterr().out.lower()
    assert not private_cli.calls


def test_filter_output_rejected_before_sender_map_read(private_cli, tmp_path, monkeypatch):
    outside = tmp_path/'unversioned'
    outside.mkdir()
    monkeypatch.setattr(filters.json, 'load', lambda *a, **k: pytest.fail('sender map read'))
    assert private_cli.compile('--out', outside/'filters.xml') == 1
    assert not (outside/'filters.xml').exists()


def test_filter_source_rejected_before_read(private_cli, tmp_path, monkeypatch):
    source = tmp_path/'sender-map.json'
    source.write_text(json.dumps(DOCTOR['sender_map']))
    monkeypatch.setattr(filters.json, 'load', lambda *a, **k: pytest.fail('sender map read'))
    assert private_cli.compile('--sender-map', source, '--out', private_cli.root/'filters.xml') == 1


def test_filter_private_output_preserves_contents(private_cli):
    output = private_cli.root/'exports/filters.xml'
    assert private_cli.compile('--out', output) == 0
    ET.fromstring(output.read_text())
    assert output.read_text() == filters.compile_filters(DOCTOR['sender_map'])


def test_filter_failed_replace_preserves_previous_output(private_cli, monkeypatch):
    output = private_cli.root/'filters.xml'
    previous = filters.compile_filters(DOCTOR['sender_map'])
    output.write_text(previous)
    import os
    def fail(*args):
        raise OSError('synthetic replacement failure')
    monkeypatch.setattr(os, 'replace', fail)
    assert private_cli.compile('--out', output) == 1
    assert output.read_text() == previous
    assert not list(private_cli.root.glob('.filters.xml-*'))


def test_filter_quoted_labels_preserve_valid_xml():
    sender_map = copy.deepcopy(DOCTOR['sender_map'])
    for address, label in sender_map['by_address'].items():
        sender_map['by_address'][address] = json.dumps(label)
    xml = filters.compile_filters(sender_map)
    root = ET.fromstring(xml)
    properties = root.findall('.//{http://schemas.google.com/apps/2006}property')
    labels = {node.get('value') for node in properties if node.get('name') == 'label'}
    assert set(sender_map['by_address'].values()).issubset(labels)


@pytest.mark.parametrize('data', RESPONSES['invalid'])
def test_quality_invalid_model_response_is_unavailable(monkeypatch, data):
    monkeypatch.setattr(review.llmcall, 'call', lambda *a, **k: SimpleNamespace(data=data))
    message = FIX['message']
    assert review.judge([(message['from'], message['subject'], DOCTOR['labels'][0])],
                        FIX['model_policy']['taxonomy'],
                        FIX['model_policy']['topic_config']['allowed_labels']) is None


def test_quality_transport_exception_is_unavailable(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('synthetic transport outage')
    monkeypatch.setattr(review.llmcall, 'call', fail)
    message = FIX['message']
    assert review.judge([(message['from'], message['subject'], DOCTOR['labels'][0])],
                        FIX['model_policy']['taxonomy']) is None


@pytest.mark.parametrize('kind', ['clean', 'wrong'])
def test_quality_complete_numbered_response_is_usable(monkeypatch, kind):
    monkeypatch.setattr(review.llmcall, 'call', lambda *a, **k: SimpleNamespace(data=RESPONSES[kind]))
    message = FIX['message']
    result = review.judge([(message['from'], message['subject'], DOCTOR['labels'][0])],
                          FIX['model_policy']['taxonomy'],
                          FIX['model_policy']['topic_config']['allowed_labels'])
    assert result == ([] if kind == 'clean' else RESPONSES[kind]['findings'])
