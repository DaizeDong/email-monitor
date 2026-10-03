"""A sender mapping cannot settle a message-specific TYPE label."""
import copy
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import em_topic
from private_storage_helpers import generator
import test_doctor_readiness as doctor_tests

CASE = generator.reliability_cases()['type_map']


@pytest.mark.parametrize('bucket,key', CASE['buckets'].items())
@pytest.mark.parametrize('label', CASE['mapped_types'])
@pytest.mark.parametrize('transport', ['abstain', 'absent', 'outage'])
def test_mapped_type_requires_positive_message_judgment(bucket, key, label, transport):
    def outage(**kwargs):
        raise RuntimeError('synthetic transport outage')
    call = {'abstain': lambda **kw: {'labels': []}, 'absent': None, 'outage': outage}[transport]
    answer = em_topic.judge(CASE['message'], CASE['taxonomy'], {bucket: {key: label}},
                           CASE['allowed'], call=call, type_labels=CASE['types'])
    assert answer['labels'] == []
    assert answer['state'] == ('unsure' if transport == 'abstain' else 'failed')


@pytest.mark.parametrize('bucket,key', CASE['buckets'].items())
def test_valid_source_mapping_survives_type_transport_outage(bucket, key):
    def outage(**kwargs):
        raise RuntimeError('synthetic transport outage')
    answer = em_topic.judge(CASE['message'], CASE['taxonomy'], {bucket: {key: CASE['source']}},
                           CASE['allowed'], call=outage, type_labels=CASE['types'])
    assert answer['state'] == 'decided'
    assert [item['label'] for item in answer['labels']] == [CASE['source']]


@pytest.mark.parametrize('bucket,key', CASE['buckets'].items())
@pytest.mark.parametrize('label', CASE['mapped_types'])
def test_doctor_rejects_type_rules_in_sender_map(bucket, key, label):
    doctor = doctor_tests.DoctorReadinessTests('test_valid_generated_config_is_ready')
    doctor.setUp()
    try:
        mapping = copy.deepcopy(generator.doctor_cases()['sender_map'])
        mapping[bucket][key] = label
        doctor.write_json('rules/sender_map.json', mapping)
        doctor.write_json('rules/labels.json', {
            generator.doctor_cases()['account']['slug']: CASE['allowed'], '_type_labels': CASE['types']})
        status, report = doctor.run_doctor()
        assert status == 1
        assert any(not row['ok'] and 'SOURCE' in row['name'] for row in report['checks'])
    finally:
        doctor.doCleanups()
