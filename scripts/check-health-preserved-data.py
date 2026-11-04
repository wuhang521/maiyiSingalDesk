"""Read-only comparison of the original 703 articles and 1969 personal states.

The private baseline never leaves the server. New records are counted, while
every original key/field is checked. No API request, commit or DML is used.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys


BASELINE = Path('/var/backups/signaldesk-task/health-push-20261030T024150Z-1e75b8/channel-before.json')
EXPECTED_ARTICLES = 703
EXPECTED_STATES = 1969
MAX_BASELINE_BYTES = 64 * 1024 * 1024


class ProbeFailure(Exception):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def load_baseline(expected_sha256):
    if not re.fullmatch(r'[a-f0-9]{64}', expected_sha256):
        raise ProbeFailure('invalid_baseline_sha256')
    info = BASELINE.lstat()
    if (BASELINE.is_symlink() or BASELINE.resolve() != BASELINE or not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > MAX_BASELINE_BYTES):
        raise ProbeFailure('private_baseline_guard')
    raw = BASELINE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ProbeFailure('baseline_sha256_mismatch')
    baseline = json.loads(raw)
    validate_baseline(baseline)
    return baseline


def validate_baseline(baseline):
    articles, states = baseline.get('articles'), baseline.get('states')
    if not isinstance(articles, list) or not isinstance(states, list):
        raise ProbeFailure('baseline_shape')
    if len(articles) != EXPECTED_ARTICLES or len(states) != EXPECTED_STATES:
        raise ProbeFailure('baseline_counts')
    ids = [row['id'] for row in articles]
    keys = [(row['operator_name'], row['article_id']) for row in states]
    if (len(set(ids)) != len(ids) or len(set(keys)) != len(keys)
            or any(row['channel_id'] not in ('health', 'demo-health') for row in articles)
            or any(row['article_id'] not in set(ids) for row in states)):
        raise ProbeFailure('baseline_keys')


def _safe_field(field):
    return field if isinstance(field, str) and re.fullmatch(r'[a-z_][a-z0-9_]{0,63}', field) else 'unexpected_column_name'


def compare_originals(baseline, current_articles, current_states, extra_counts=None):
    """Pure comparison; values/keys only enter hashes or column-name counts."""
    validate_baseline(baseline)
    originals, original_states = baseline['articles'], baseline['states']
    by_id = {row['id']: row for row in current_articles}
    by_state = {(row['operator_name'], row['article_id']): row for row in current_states}
    if len(by_id) != len(current_articles) or len(by_state) != len(current_states):
        raise ProbeFailure('duplicate_current_keys')
    differences = {'articles': {'changed': Counter(), 'missing_field': Counter(), 'extra_field': Counter()},
                   'states': {'changed': Counter(), 'missing_field': Counter(), 'extra_field': Counter()}}
    missing = {'articles': 0, 'states': 0}
    permitted_channel_moves = 0

    def compare(expected, actual, category):
        nonlocal permitted_channel_moves
        for field in set(expected) | set(actual):
            name = _safe_field(field)
            if field not in actual:
                differences[category]['missing_field'][name] += 1
            elif field not in expected:
                differences[category]['extra_field'][name] += 1
            elif type(expected[field]) is not type(actual[field]) or digest(expected[field]) != digest(actual[field]):
                if (category == 'articles' and field == 'channel_id'
                        and expected[field] == 'demo-health' and actual[field] == 'health'):
                    permitted_channel_moves += 1
                else:
                    differences[category]['changed'][name] += 1

    selected_articles, selected_states = [], []
    # Baseline order avoids a changed SQL collation/order causing false SHA
    # differences. All keys remain exact, case-sensitive Python identities.
    for expected in originals:
        actual = by_id.get(expected['id'])
        if actual is None:
            missing['articles'] += 1
        else:
            selected_articles.append(actual)
            compare(expected, actual, 'articles')
    for expected in original_states:
        actual = by_state.get((expected['operator_name'], expected['article_id']))
        if actual is None:
            missing['states'] += 1
        else:
            selected_states.append(actual)
            compare(expected, actual, 'states')
    expected_hashes = {
        'article_ids_sha256': digest([row['id'] for row in originals]),
        'article_content_and_times_sha256': digest([{key: value for key, value in row.items() if key != 'channel_id'} for row in originals]),
        'all_personal_states_sha256': digest(original_states),
    }
    current_hashes = {
        'article_ids_sha256': digest([row['id'] for row in selected_articles]),
        'article_content_and_times_sha256': digest([{key: value for key, value in row.items() if key != 'channel_id'} for row in selected_articles]),
        'all_personal_states_sha256': digest(selected_states),
    }
    serial_differences = {kind: {category: dict(sorted(counts.items())) for category, counts in categories.items()}
                          for kind, categories in differences.items()}
    changed = any(counts for categories in differences.values() for counts in categories.values())
    canonical = all(row.get('channel_id') == 'health' for row in selected_articles)
    passed = (not any(missing.values()) and not changed and canonical and expected_hashes == current_hashes)
    return {'passed': passed, 'read_only': True, 'scope': 'original_primary_key_subset_all_fields',
            'baseline_article_count': len(originals), 'baseline_state_count': len(original_states),
            'matched_original_articles': len(selected_articles), 'matched_original_states': len(selected_states),
            'missing_original_keys': missing, 'field_difference_counts': serial_differences,
            'permitted_demo_health_to_health_moves': permitted_channel_moves,
            'all_original_articles_on_health': canonical,
            'expected_original_fingerprints': expected_hashes, 'current_original_fingerprints': current_hashes,
            'additional_records_counted_and_ignored': extra_counts or {},
            'identifiers_names_values_or_body_exported': False,
            'api_calls': 0, 'business_writes': 0, 'messages_sent': 0}


def read_current(baseline):
    # Imports/settings stay inside execution; importing this helper for pure
    # synthetic tests never opens configuration or a database connection.
    from sqlalchemy import case, func, select
    from sqlalchemy.orm import Session
    from app.channel_reconcile import rows
    from app.database import engine
    from app.models import Article, ArticleUserState

    if engine.dialect.name != 'mysql':
        raise ProbeFailure('expected_mysql_database')
    ids = [row['id'] for row in baseline['articles']]
    original_keys = {(row['operator_name'], row['article_id']) for row in baseline['states']}
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql('START TRANSACTION READ ONLY')
            with Session(bind=connection, autoflush=False) as db:
                articles = rows(db, Article, Article.id.in_(ids), [Article.id])
                states = rows(db, ArticleUserState, ArticleUserState.article_id.in_(ids),
                              [ArticleUserState.operator_name, ArticleUserState.article_id])
                new_condition = (Article.channel_id == 'health') & ~Article.id.in_(ids)
                new_articles = db.execute(select(func.count(), func.sum(case((Article.id.like('test:%'), 1), else_=0)))
                                          .select_from(Article).where(new_condition)).one()
                new_states = db.execute(select(func.count(), func.sum(case((Article.id.like('test:%'), 1), else_=0)))
                                        .select_from(ArticleUserState).join(Article, Article.id == ArticleUserState.article_id)
                                        .where(new_condition)).one()
                extras = {'new_health_articles': int(new_articles[0]),
                          'new_test_health_articles': int(new_articles[1] or 0),
                          'new_states_on_new_health_articles': int(new_states[0]),
                          'new_states_on_new_test_articles': int(new_states[1] or 0),
                          'new_state_keys_on_original_articles': sum(
                              (row['operator_name'], row['article_id']) not in original_keys for row in states)}
                db.rollback()
            connection.rollback()
        return articles, states, extras
    finally:
        engine.dispose()


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        print(json.dumps({'passed': False, 'error': 'invalid_arguments', 'business_writes': 0}))
        raise SystemExit(2)


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument('--baseline-sha256', required=True)
    args = parser.parse_args(argv)
    sys.dont_write_bytecode = True
    try:
        baseline = load_baseline(args.baseline_sha256)
        # The normal managed installation resolves app/configuration here.
        os.chdir('/opt/signaldesk-task')
        sys.path.insert(0, '/opt/signaldesk-task')
        current_articles, current_states, extras = read_current(baseline)
        report = compare_originals(baseline, current_articles, current_states, extras)
        report['baseline_sha256_verified'] = True
    except ProbeFailure as error:
        report = {'passed': False, 'error': str(error), 'business_writes': 0,
                  'identifiers_names_values_or_body_exported': False}
    except Exception as error:
        report = {'passed': False, 'error_type': type(error).__name__, 'business_writes': 0,
                  'identifiers_names_values_or_body_exported': False}
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
