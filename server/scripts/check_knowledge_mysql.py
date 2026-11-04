"""Concurrency acceptance in a uniquely named disposable MySQL schema only."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import sys
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--test-env', required=True, type=Path, help='Dedicated test account configuration; never the production .env')
    args = parser.parse_args()
    candidate = args.candidate.resolve()
    if candidate.parent != Path('/var/tmp') or not candidate.name.startswith('signaldesk-knowledge-candidate-'):
        raise ValueError('Expected private candidate')
    from dotenv import dotenv_values
    from sqlalchemy import create_engine, select, func
    from sqlalchemy.engine import URL, make_url
    if args.test_env.resolve() == Path('/opt/signaldesk-task/.env'):
        raise ValueError('Production environment is not accepted for shadow database tests')
    values = dotenv_values(args.test_env)
    live = make_url(values['KNOWLEDGE_TEST_DATABASE_URL'])
    if live.get_backend_name() != 'mysql':
        print(json.dumps(dict(passed=False, blocked='Production is not MySQL; shadow test not applicable'))); return 2
    schema = 'signaldesk_kb_test_' + uuid.uuid4().hex[:16]
    assert re.fullmatch(r'signaldesk_kb_test_[a-f0-9]{16}', schema) and schema != live.database
    # URL.set(database=None) retains the previous database instead of clearing
    # it. Connect without selecting a schema before creating the temporary one.
    admin = create_engine(URL.create(live.drivername, username=live.username, password=live.password,
        host=live.host, port=live.port, query=live.query), pool_pre_ping=True)
    created = False
    app_engine = None
    report = dict(schema=schema, production_tables_written=False)
    try:
        with admin.begin() as connection:
            connection.exec_driver_sql('CREATE DATABASE `' + schema + '` CHARACTER SET utf8mb4')
        created = True
        os.environ['TASK_DATABASE_URL'] = live.set(database=schema).render_as_string(hide_password=False)
        os.environ['TASK_API_KEY'] = 'synthetic-mysql-test'
        os.environ['TASK_KNOWLEDGE_STORAGE_DIR'] = str(candidate / ('synthetic-originals-' + schema))
        os.environ['TASK_KNOWLEDGE_CLOUD_ASR_PROVIDER'] = 'qwen'
        os.environ['TASK_KNOWLEDGE_CLOUD_ASR_ENABLED'] = 'true'
        os.environ['TASK_KNOWLEDGE_QWEN_API_KEY'] = 'synthetic-not-sent'
        os.chdir(candidate); sys.path.insert(0, str(candidate))
        from app.main import app
        from app.database import Base, engine, SessionLocal
        from app.auth import create_user_session
        from app.config import get_settings
        from app.models import Staff, UserAccount
        from app.knowledge_models import KnowledgeCloudUsage, KnowledgeJob, KnowledgeCostReservation
        from fastapi.testclient import TestClient
        app_engine = engine
        assert engine.url.database == schema
        Base.metadata.create_all(engine)
        headers = []
        with SessionLocal() as db:
            for index in range(2):
                user = UserAccount(username='synthetic-race-' + str(index), staff=Staff(name='Synthetic test ' + str(index)), role='member', password_hash='not-used')
                db.add(user); db.flush(); token, _ = create_user_session(db, user)
                headers.append({'Authorization': 'Bearer ' + token})
            db.commit()
        with TestClient(app) as client:
            def media(owner, index):
                item = client.post('/api/v1/knowledge/items', headers=headers[owner], json={'title': 'Synthetic race ' + str(index)}).json()
                response = client.post('/api/v1/knowledge/items/' + item['id'] + '/assets', params={'filename': 'sample.wav', 'revision': item['revision']}, headers={**headers[owner], 'Content-Type': 'application/octet-stream'}, content=b'RIFF-synthetic-only')
                assert response.status_code == 200
                return response.json()
            def submit(owner, item):
                return client.post('/api/v1/knowledge/items/' + item['id'] + '/assets/' + item['assets'][0]['id'] + '/asr', headers=headers[owner], json={'revision': item['revision'], 'cloud_consent': True}).status_code
            settings = get_settings()
            settings.knowledge_paid_daily_limit = 1
            first = [media(0, i) for i in range(2)]
            with ThreadPoolExecutor(max_workers=2) as workers:
                statuses = sorted(workers.map(lambda item: submit(0, item), first))
            assert statuses == [200, 429], statuses
            settings.knowledge_paid_daily_limit = 20
            settings.knowledge_money_budget_enabled = True
            settings.knowledge_budget_daily_cny = '0.001'
            settings.knowledge_budget_monthly_cny = '0.010'
            settings.knowledge_request_ceiling_cny = '{"qwen_asr":"0.001"}'
            second = [media(1, i) for i in range(2)]
            with ThreadPoolExecutor(max_workers=2) as workers:
                money_statuses = sorted(workers.map(lambda item: submit(1, item), second))
            assert money_statuses == [200, 429], money_statuses
            with SessionLocal() as db:
                assert db.scalar(select(func.count()).select_from(KnowledgeCloudUsage)) == 2
                assert db.scalar(select(func.count()).select_from(KnowledgeJob).where(KnowledgeJob.mode == 'qwen_asr')) == 2
                assert db.scalar(select(func.count()).select_from(KnowledgeCostReservation)) == 2
            report.update(passed=True, count_race=statuses, money_race=money_statuses, accepted_paid_jobs=2, provider_calls=0)
    finally:
        if app_engine is not None: app_engine.dispose()
        if created:
            assert re.fullmatch(r'signaldesk_kb_test_[a-f0-9]{16}', schema) and schema != live.database
            with admin.begin() as connection: connection.exec_driver_sql('DROP DATABASE `' + schema + '`')
            report['shadow_schema_removed'] = True
        admin.dispose()
        print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps(dict(passed=False, error_type=type(error).__name__, note='No provider calls; inspect isolated candidate, do not expose connection credentials.')))
        raise SystemExit(1)
