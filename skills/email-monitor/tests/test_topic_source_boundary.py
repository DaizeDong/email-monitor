"""A sender mapping cannot settle a message-specific TYPE label."""
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import em_topic
from private_storage_helpers import generator

CASE = generator.reliability_cases()['type_map']


@pytest.mark.parametrize('bucket,key', [('by_address', CASE['buckets']['by_address'])])
@pytest.mark.parametrize('label', ['Receipt'])
@pytest.mark.parametrize('transport', ['abstain'])
def test_mapped_type_requires_positive_message_judgment(bucket, key, label, transport):
    def outage(**kwargs):
        raise RuntimeError('synthetic transport outage')
    call = {'abstain': lambda **kw: {'labels': []}, 'absent': None, 'outage': outage}[transport]
    answer = em_topic.judge(CASE['message'], CASE['taxonomy'], {bucket: {key: label}},
                           CASE['allowed'], call=call, type_labels=CASE['types'])
    assert answer['labels'] == []
    assert answer['state'] == ('unsure' if transport == 'abstain' else 'failed')
