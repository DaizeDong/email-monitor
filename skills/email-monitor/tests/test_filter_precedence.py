"""Exported positives must not contradict the kernel's narrower sender mapping."""
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import em_filters
import em_topic
from private_storage_helpers import generator

CASE = generator.reliability_cases()['filter_precedence']


def exported_filters(mapping):
    root = ET.fromstring(em_filters.compile_filters(mapping))
    return [{prop.attrib['name']: prop.attrib['value'] for prop in entry
             if prop.tag.endswith('property')}
            for entry in root if entry.tag.endswith('entry')]


def test_conflicting_domain_is_reported_without_overriding_address_precedence():
    mapping = {'by_address': {CASE['address']: CASE['specific']},
               'by_domain': {CASE['domain']: CASE['general'], CASE['unrelated']: CASE['general']}}
    assert em_topic.pregate({'from': CASE['address']}, mapping)[0]['label'] == CASE['specific']
    rows = exported_filters(mapping)
    assert {'from': CASE['address'], 'label': CASE['specific']} in rows
    assert all('*@' + CASE['domain'] not in row['from'].split('|') for row in rows)
    assert any('*@' + CASE['unrelated'] in row['from'].split('|') for row in rows)
    assert em_filters.uncompilable(mapping)['by_domain_conflicts'] == 1


def test_same_label_address_and_domain_remain_compilable():
    mapping = {'by_address': {CASE['address']: CASE['specific']},
               'by_domain': {CASE['domain']: CASE['specific']}}
    assert any('*@' + CASE['domain'] in row['from'].split('|') for row in exported_filters(mapping))
    assert em_filters.uncompilable(mapping)['by_domain_conflicts'] == 0


def test_narrower_domain_conflict_is_also_reported():
    mapping = {'by_domain': {CASE['domain']: CASE['general'], CASE['narrow_domain']: CASE['specific']}}
    rows = exported_filters(mapping)
    assert all('*@' + CASE['domain'] not in row['from'].split('|') for row in rows)
    assert any('*@' + CASE['narrow_domain'] in row['from'].split('|') for row in rows)
    assert em_filters.uncompilable(mapping)['by_domain_conflicts'] == 1
