"""Pure synthetic preservation checks; no config, database or live API."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[2] / 'scripts/check-health-preserved-data.py'
SPEC = importlib.util.spec_from_file_location('health_preservation_probe_under_test', SOURCE)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


@pytest.fixture
def data():
    stamp = {'$datetime': '2026-10-30T02:00:00.123456'}
    baseline = {
        'articles': [{'id': f'original-{index:04}', 'channel_id': 'demo-health' if index < 104 else 'health',
                      'title': f'synthetic-title-{index}', 'body': 'synthetic-private-body',
                      'created_at': stamp.copy(), 'updated_at': stamp.copy(), 'tags': ['synthetic']}
                     for index in range(703)],
        'states': [{'operator_name': f'synthetic-operator-{index // 703}',
                    'article_id': f'original-{index % 703:04}', 'is_read': False, 'is_saved': True,
                    'deleted_at': None, 'permanently_deleted_at': None, 'updated_at': stamp.copy()}
                   for index in range(1969)],
    }
    current = deepcopy(baseline)
    for row in current['articles']:
        row['channel_id'] = 'health'
    return baseline, current


def compare(data):
    baseline, current = data
    return probe.compare_originals(baseline, current['articles'], current['states'])


def test_permitted_migration_preserves_every_original_field_and_hash(data):
    report = compare(data)
    assert report['passed'] and report['permitted_demo_health_to_health_moves'] == 104
    assert report['matched_original_articles'] == 703 and report['matched_original_states'] == 1969
    assert report['expected_original_fingerprints'] == report['current_original_fingerprints']


def test_new_test_article_and_state_do_not_change_original_subset(data):
    baseline, current = data
    current['articles'].append({'id': 'test:synthetic-new', 'channel_id': 'health', 'body': 'synthetic-new-body'})
    current['states'].append({'operator_name': 'synthetic-new-operator', 'article_id': 'test:synthetic-new'})
    report = probe.compare_originals(baseline, current['articles'], current['states'],
        {'new_health_articles': 1, 'new_test_health_articles': 1, 'new_states_on_new_test_articles': 1})
    assert report['passed'] and report['matched_original_articles'] == 703
    assert report['additional_records_counted_and_ignored']['new_test_health_articles'] == 1


def test_current_query_order_does_not_create_false_fingerprint_difference(data):
    data[1]['articles'].reverse()
    data[1]['states'].reverse()
    assert compare(data)['passed']


@pytest.mark.parametrize('category,field,value', [
    ('articles', 'body', 'do-not-output-private-new-body'),
    ('articles', 'updated_at', {'$datetime': '2026-10-30T02:00:00.123457'}),
    ('states', 'updated_at', {'$datetime': '2026-10-30T02:00:00.123457'}),
    ('states', 'is_read', True),
    ('states', 'is_saved', False),
    ('states', 'deleted_at', {'$datetime': '2026-10-30T02:00:00.000000'}),
    ('states', 'is_read', 0),
])
def test_original_field_and_microsecond_changes_fail_with_column_counts_only(data, category, field, value):
    data[1][category][0][field] = value
    report = compare(data)
    assert not report['passed'] and report['field_difference_counts'][category]['changed'][field] == 1
    serialized = json.dumps(report)
    for secret in ('do-not-output-private-new-body', 'synthetic-private-body', 'synthetic-operator-0', 'original-0000'):
        assert secret not in serialized


@pytest.mark.parametrize('category', ['articles', 'states'])
def test_missing_original_primary_key_is_reported_without_its_value(data, category):
    data[1][category].pop(0)
    report = compare(data)
    assert not report['passed'] and report['missing_original_keys'][category] == 1
    assert 'original-0000' not in json.dumps(report)


def test_only_legacy_to_canonical_channel_exception_is_allowed(data):
    data[1]['articles'][104]['channel_id'] = 'demo-health'
    report = compare(data)
    assert not report['passed'] and report['field_difference_counts']['articles']['changed']['channel_id'] == 1


def test_unmigrated_legacy_row_fails_even_when_other_fields_match(data):
    data[1]['articles'][0]['channel_id'] = 'demo-health'
    assert not compare(data)['passed']


@pytest.mark.parametrize('operation', ['missing', 'extra'])
def test_field_set_changes_cannot_hide_behind_known_columns(data, operation):
    if operation == 'missing':
        del data[1]['articles'][0]['body']
        field = 'body'
    else:
        data[1]['articles'][0]['new_column'] = 'synthetic-hidden'
        field = 'new_column'
    report = compare(data)
    assert not report['passed']
    assert report['field_difference_counts']['articles'][operation + '_field'][field] == 1


def test_unknown_unsafe_column_name_is_never_exported(data):
    data[1]['states'][0]['https://example.invalid/do-not-output'] = 'private'
    report = compare(data)
    assert not report['passed']
    assert report['field_difference_counts']['states']['extra_field'] == {'unexpected_column_name': 1}
    assert 'https://' not in json.dumps(report)


def test_duplicate_current_primary_key_is_not_silently_dropped(data):
    data[1]['states'].append(deepcopy(data[1]['states'][0]))
    with pytest.raises(probe.ProbeFailure, match='duplicate_current_keys'):
        compare(data)


def test_wrong_baseline_counts_fail_before_current_comparison(data):
    data[0]['articles'].pop()
    with pytest.raises(probe.ProbeFailure, match='baseline_counts'):
        compare(data)


def test_invalid_baseline_sha_never_reads_config_or_database(monkeypatch, capsys):
    monkeypatch.setattr(probe, 'read_current', lambda value: pytest.fail('Database must not be reached'))
    assert probe.main(['--baseline-sha256', 'not-a-sha']) == 1
    assert json.loads(capsys.readouterr().out)['error'] == 'invalid_baseline_sha256'
