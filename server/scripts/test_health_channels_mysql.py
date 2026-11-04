"""Isolated health-channel MySQL HTTP/concurrency and reversible-merge acceptance.

Never uses production data: refuses non-test/nonlocal databases and nonempty
schemas. Reports contain only booleans, counts, statuses and SHA256 digests.
--local-sqlite is a serial transport/migration self-test, not MySQL lock proof.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import quote


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def source_server(root):
    root = root.resolve()
    server = root / 'server'
    if not all((server / 'app' / name).is_file() for name in
               ('articles.py', 'channel_aliases.py', 'channel_reconcile.py')):
        raise ValueError('InvalidSourceStage')
    if (root / '.env').exists() or (server / '.env').exists():
        raise ValueError('PrivateEnvironmentFileNotAllowed')
    return server


def mysql_url(raw):
    from sqlalchemy.engine import make_url
    url = make_url(raw)
    if url.get_backend_name() != 'mysql':
        raise ValueError('MySQLRequired')
    if not re.fullmatch(r'signaldesk_kb_test_health_[A-Za-z0-9_]+', url.database or ''):
        raise ValueError('TestDatabasePrefixRequired')
    if url.host not in (None, '', 'localhost', '127.0.0.1', '::1'):
        raise ValueError('LocalTestDatabaseRequired')
    return url


def child_server(root, port, run_id):
    if os.environ.get('HEALTH_CHANNEL_TEST_CHILD') != run_id:
        raise ValueError('OwnedChildRequired')
    server = source_server(root)
    from sqlalchemy.engine import make_url
    raw = os.environ['TASK_DATABASE_URL']
    url = make_url(raw)
    if url.get_backend_name() == 'mysql':
        mysql_url(raw)
    elif url.get_backend_name() != 'sqlite' or os.environ.get('HEALTH_CHANNEL_TEST_SQLITE') != run_id:
        raise ValueError('TestDatabaseRequired')
    os.chdir(server)
    sys.path.insert(0, str(server))
    from fastapi import FastAPI
    import uvicorn
    from app.articles import router
    app = FastAPI()
    app.include_router(router)

    @app.get('/__health_fixture_ready')
    def ready():
        return {'run_id': run_id}

    uvicorn.run(app, host='127.0.0.1', port=port, access_log=False, log_level='error')


def main(args):
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    result = {'passed': False, 'mode': 'local_sqlite_serial_self_test' if args.local_sqlite else 'mysql_actual_http_concurrency',
              'actual_http': False, 'mysql_concurrency_executed': False,
              'production_accessed': False, 'real_user_data_read': False,
              'external_notification_sent': False, 'cloud_calls': 0,
              'credentials_or_database_url_printed': False, 'checks': {}, 'cleanup': {}}
    engine = None
    owned_tables = set()
    child = None
    work = None
    stage = 'preflight'
    try:
        args.source_root = args.source_root.resolve()
        server = source_server(args.source_root)
        result['source_sha256'] = {name:digest_bytes((server/'app'/name).read_bytes())
                                  for name in ('articles.py','channel_aliases.py','channel_reconcile.py','schemas.py')}
        result['runner_sha256'] = digest_bytes(Path(__file__).read_bytes())
        work = tempfile.TemporaryDirectory(prefix='health-channel-test-', dir=str(output.parent))
        working = Path(work.name).resolve()
        if working.parent != output.parent:
            raise ValueError('OwnedWorkspaceRequired')
        from sqlalchemy import create_engine, inspect, select, delete
        from sqlalchemy.engine import URL
        if args.local_sqlite:
            url = URL.create('sqlite', database=str(working / 'owned-test.sqlite'))
            connection_args = {'check_same_thread': False}
        else:
            raw = os.environ.get('KNOWLEDGE_TEST_DATABASE_URL', '')
            if not raw:
                raise ValueError('TestDatabaseEnvironmentRequired')
            url = mysql_url(raw)
            connection_args = {}
        check_engine = create_engine(url, pool_pre_ping=True, connect_args=connection_args)
        try:
            inspector = inspect(check_engine)
            if inspector.get_table_names() or inspector.get_view_names():
                raise ValueError('EmptyTestSchemaRequired')
            if not args.local_sqlite:
                with check_engine.connect() as connection:
                    version = connection.exec_driver_sql('SELECT VERSION()').scalar()
                    result['mysql_8_detected'] = str(version).startswith('8.')
                    if not result['mysql_8_detected']:
                        raise ValueError('MySQL8Required')
        finally:
            check_engine.dispose()
        result['checks']['empty_schema_guard_passed'] = True
        os.environ['TASK_DATABASE_URL'] = url.render_as_string(hide_password=False)
        key = secrets.token_urlsafe(32)
        os.environ['TASK_API_KEY'] = key
        os.chdir(server)
        sys.path.insert(0, str(server))
        from app.database import Base, engine, SessionLocal
        from app.models import Article, ArticleUserState, SmartChannel
        from app.channel_aliases import SmartChannelAlias
        from app.channel_reconcile import capture, decode, digest, fingerprints, reconcile, restore
        from app.articles import article_hash
        from app.schemas import ArticleInput
        # Only this empty schema is owned. Even a partially failed CREATE is
        # cleaned from the initial, reviewed metadata table set in finally.
        owned_tables = set(Base.metadata.tables)
        Base.metadata.create_all(engine)
        if set(inspect(engine).get_table_names()) != owned_tables:
            raise ValueError('UnexpectedCreatedSchema')
        result['created_table_count'] = len(owned_tables)
        stage = 'seed_synthetic_migration_fixture'
        stamp = datetime.now(timezone.utc).replace(tzinfo=None,microsecond=123456)
        with SessionLocal.begin() as db:
            for channel_id, topic, state, limit in (
                ('health', '', 'draft', 100), ('demo-health', 'synthetic-old-topic', 'active', 20),
                ('other', 'synthetic-unrelated-topic', 'paused', 17)):
                db.add(SmartChannel(id=channel_id, name='Synthetic '+channel_id,
                                    description='Fixture description', prompt='Fixture business rules',
                                    topic=topic, server='https://push.example.test', schedule='09:00',
                                    max_items=limit, include_terms=['business'], exclude_terms=['diet'],
                                    color='green', state=state, created_at=stamp-timedelta(days=4), updated_at=stamp))
            for number in range(4):
                article_id = 'migration-fixture-'+str(number)
                db.add(Article(id=article_id, channel_id='health' if number < 2 else 'demo-health',
                               title='Synthetic title '+str(number), body='Synthetic body '+str(number),
                               source='fixture.example.test', click_url='https://fixture.example.test/'+str(number),
                               tags=['synthetic',str(number)], priority=number+1,
                               published_at=stamp-timedelta(hours=number+1), created_at=stamp-timedelta(days=1),
                               updated_at=stamp, content_hash=digest_bytes(('fixture-'+str(number)).encode())))
                db.flush()
                for person in range(3):
                    db.add(ArticleUserState(operator_name='synthetic-operator-'+str(person), article_id=article_id,
                                            read=person != 0, saved=person != 1,
                                            feedback='useful' if person == 1 else None,
                                            deleted_at=stamp if person > 0 else None,
                                            purge_at=stamp+timedelta(days=7) if person > 0 else None,
                                            permanently_deleted_at=stamp if person == 2 else None,
                                            state_updated_at_ms=1000+number*10+person, updated_at=stamp))
        with SessionLocal() as db:
            before = capture(db)
        original_fingerprint = fingerprints(before)
        before_hash = digest(before)
        private_backup = working / 'synthetic-backup.private.json'
        private_backup.write_text(json.dumps(before, ensure_ascii=False), encoding='utf-8')
        private_backup.chmod(0o600)
        restored_backup = json.loads(private_backup.read_text(encoding='utf-8'))
        assert digest(restored_backup) == before_hash, 'BackupSerializationMismatch'
        datetime_values = [value['$datetime'] for name in ('articles','states','channels')
                           for row in restored_backup[name] for value in row.values()
                           if isinstance(value,dict) and '$datetime' in value]
        assert datetime_values and all('.123456' in value for value in datetime_values), 'MicrosecondFixtureMissing'
        result['checks']['synthetic_backup_serialization_passed'] = True
        result['backup_sha256'] = digest_bytes(private_backup.read_bytes())
        result['datetime_values_with_microseconds'] = len(datetime_values)
        stage = 'migration_failure_rollback'
        for failure in ('connection', 'articles'):
            raised = False
            try:
                with SessionLocal.begin() as db:
                    reconcile(db, 'synthetic-publisher-topic', 'https://push.example.test/', fail_at=failure)
            except RuntimeError:
                raised = True
            assert raised, 'FaultInjectionDidNotRaise'
            with SessionLocal() as db:
                assert digest(capture(db)) == before_hash, 'FailureDidNotRollback'
        result['checks']['both_migration_failures_rollback_exactly'] = True
        stage = 'migration_idempotence_and_restore'
        with SessionLocal.begin() as db:
            merged = reconcile(db, 'synthetic-publisher-topic', 'https://push.example.test/')
        with SessionLocal() as db:
            after = capture(db)
        after_hash = digest(after)
        assert fingerprints(after) == original_fingerprint, 'MigrationChangedOriginalsOrStates'
        assert len(after['channels']) == 1 and after['channels'][0]['id'] == 'health', 'DuplicateChannelRemains'
        assert all(row['channel_id'] == 'health' for row in after['articles']), 'ArticleAliasRemains'
        assert len(after['aliases']) == 1, 'PersistentAliasMissing'
        with SessionLocal.begin() as db:
            second = reconcile(db, 'synthetic-publisher-topic', 'https://push.example.test/')
        with SessionLocal() as db:
            assert digest(capture(db)) == after_hash, 'SecondMigrationNotIdempotent'
        assert second['moved_articles'] == 0 and merged['moved_articles'] == 2, 'UnexpectedMovedCount'
        with SessionLocal.begin() as db:
            restore(db, restored_backup, after_hash)
        with SessionLocal() as db:
            assert digest(capture(db)) == before_hash, 'RestoreNotExact'
        result['checks'].update(migration_only_article_channel_changes=True,
                                all_personal_states_unchanged=True, repeated_migration_idempotent=True,
                                restored_before_image_all_fields_microseconds_exact=True)
        result['migration_fingerprints'] = original_fingerprint
        result['migration_before_sha256'] = before_hash
        result['migration_after_sha256'] = after_hash
        # Reapply once, then prove restore refuses a newer after-image.
        with SessionLocal.begin() as db:
            reconcile(db, 'synthetic-publisher-topic', 'https://push.example.test/')
            db.get(SmartChannel, 'health').description = 'Synthetic newer work'
        refused = False
        try:
            with SessionLocal.begin() as db:
                restore(db, restored_backup, after_hash)
        except ValueError:
            refused = True
        assert refused, 'RestoreOverwroteNewerData'
        result['checks']['restore_refuses_newer_after_image'] = True
        # The migration fixture proof is complete. Remove only our fixture rows
        # so the subsequent HTTP groups have exact, independent count assertions.
        with SessionLocal.begin() as db:
            db.execute(delete(ArticleUserState))
            db.execute(delete(Article))
        stage = 'start_owned_http_process'
        run_id = secrets.token_hex(16)
        child_env = dict(os.environ, HEALTH_CHANNEL_TEST_CHILD=run_id)
        if args.local_sqlite:
            child_env['HEALTH_CHANNEL_TEST_SQLITE'] = run_id
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1',0))
            port = reserved.getsockname()[1]
        flags = getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name == 'nt' else 0
        child = subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--serve',
                                  '--source-root',str(args.source_root.resolve()),'--port',str(port),
                                  '--run-id',run_id], env=child_env, cwd=server,
                                 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags)
        import httpx
        base = 'http://127.0.0.1:'+str(port)
        headers = {'X-SignalDesk-Key':key,'X-SignalDesk-Operator':'synthetic-concurrent-operator'}
        with httpx.Client(base_url=base,headers=headers,trust_env=False,timeout=25,follow_redirects=False) as client:
            deadline = time.monotonic()+25
            while True:
                if child.poll() is not None:
                    raise RuntimeError('OwnedHTTPProcessExited')
                try:
                    if client.get('/__health_fixture_ready').json() == {'run_id':run_id}:
                        break
                except (httpx.HTTPError,ValueError):
                    pass
                if time.monotonic() > deadline:
                    raise TimeoutError('OwnedHTTPStartupTimeout')
                time.sleep(0.1)
            result['actual_http'] = True
            stage = 'managed_channel_http_guards'
            listed = client.get('/api/v1/channels')
            assert listed.status_code == 200, 'ChannelListFailed'
            listing = listed.json()
            assert {row['id'] for row in listing} == {'health','other'}, 'UnexpectedChannelList'
            canonical = next(row for row in listing if row['id'] == 'health')
            assert canonical['managed'] and canonical['aliases'] == ['demo-health'], 'AliasResponseContractFailed'
            with SessionLocal() as db:
                channel_before = digest(capture(db))
            for channel_id in ('demo-health','health'):
                response = client.post('/api/v1/channels',json={'id':channel_id,'name':'Obsolete cache',
                                                               'topic':'','server':'','state':'draft','maxItems':20})
                assert response.status_code == 200 and response.json() == {'id':'health'}, 'ManagedPostOverwroteConnection'
            assert client.delete('/api/v1/channels/demo-health').status_code == 204, 'AliasDeleteFailed'
            assert client.delete('/api/v1/channels/health').status_code == 409, 'ManagedDeleteWasAllowed'
            with SessionLocal() as db:
                assert digest(capture(db)) == channel_before, 'ChannelReplayChangedConfiguration'
            result['checks']['canonical_list_and_obsolete_posts_deletes_safe'] = True
            for route in ('batch','sync'):
                control = {'id':'synthetic-control-'+route,'channelId':'demo-health',
                           'title':'Synthetic delayed status','body':'Synthetic status without an article heading',
                           'timestamp':1786400000123,'tags':['health_status'],'stateUpdatedAt':99999}
                response = client.post('/api/v1/articles/'+route,json={'items':[control]})
                assert response.status_code == 200 and response.json() == {'accepted':0}, 'ControlMessageWasStored'
            with SessionLocal() as db:
                assert not list(db.scalars(select(Article))), 'ControlMessageArticleCreated'
                assert not list(db.scalars(select(ArticleUserState))), 'ControlMessageStateCreated'
            result['checks']['reserved_status_is_not_article_or_state'] = True

            def article(article_id, group):
                return {'id':article_id,'channelId':'demo-health','title':'Synthetic HTTP '+group,
                        'body':'Synthetic HTTP body '+group,'source':'fixture.example.test',
                        'clickUrl':'https://fixture.example.test/'+group,'tags':['synthetic'],
                        'timestamp':1786400000123,'priority':4}

            workers = 1 if args.local_sqlite else 6
            result['concurrency_workers'] = workers

            def concurrent_posts(route, values):
                barrier = threading.Barrier(len(values)) if workers > 1 else None

                def submit(value):
                    if barrier is not None:
                        barrier.wait(timeout=20)
                    with httpx.Client(base_url=base,headers=headers,trust_env=False,timeout=35,
                                      follow_redirects=False) as connection:
                        response = connection.post(route,json={'items':[value]})
                        return response.status_code

                with ThreadPoolExecutor(max_workers=workers) as executor:
                    statuses = list(executor.map(submit,values))
                result.setdefault('http_attempts',{})[stage] = sorted(statuses)
                assert statuses == [200]*len(values), 'ConcurrentHTTPStatusFailure'
                return sorted(statuses)

            stage = 'same_id_concurrent_batch'
            first = article('http-same-id','same-id')
            statuses = concurrent_posts('/api/v1/articles/batch',[dict(first) for _ in range(6)])
            with SessionLocal() as db:
                group = list(db.scalars(select(Article).where(Article.id == first['id'])))
                assert len(group) == 1 and group[0].channel_id == 'health', 'SameIdDuplicateOrAlias'
                original = {c.name:getattr(group[0],c.name) for c in Article.__table__.columns}
            result['same_id_batch'] = {'statuses':statuses,'article_count':1,'no_http_500':True}
            stage = 'same_hash_different_id_concurrent_batch'
            values = [article(('ntfy-fixture:' if i%2 else 'daily:health:fixture:')+str(i),'same-hash')
                      for i in range(6)]
            statuses = concurrent_posts('/api/v1/articles/batch',values)
            expected_hash = article_hash(ArticleInput(**values[0]))
            with SessionLocal() as db:
                group = list(db.scalars(select(Article).where(Article.content_hash == expected_hash)))
                assert len(group) == 1 and group[0].channel_id == 'health', 'SameHashDuplicateOrAlias'
                resolved_id=group[0].id
                resolved_original={c.name:getattr(group[0],c.name) for c in Article.__table__.columns}
            result['different_id_same_hash_batch'] = {'statuses':statuses,'article_count':1,
                                                       'content_sha256':expected_hash,'no_http_500':True}
            stage = 'same_operator_concurrent_state_sync'
            timestamps = [100,600,300,900,500,200]
            values = [{**first,'body':'Obsolete synthetic cache body','timestamp':1,'read':t==900,
                       'saved':t==900,'feedback':'useful' if t==900 else None,'deletedAt':None,
                       'purged':False,'stateUpdatedAt':t} for t in timestamps]
            statuses = concurrent_posts('/api/v1/articles/sync',values)
            with SessionLocal() as db:
                states = list(db.scalars(select(ArticleUserState)))
                assert len(states) == 1, 'ConcurrentStateDuplicate'
                state = states[0]
                assert state.article_id == first['id'] and state.state_updated_at_ms == 900, 'LatestStateLost'
                assert state.read and state.saved and state.feedback == 'useful', 'LatestStateFieldsLost'
                current = db.get(Article,first['id'])
                assert {c.name:getattr(current,c.name) for c in Article.__table__.columns} == original, 'CachedContentOverwroteOriginal'
                assert len(list(db.scalars(select(Article)))) == 2, 'UnexpectedFinalArticleCount'
            result['same_operator_state_sync'] = {'statuses':statuses,'state_count':1,
                                                   'greatest_state_timestamp_wins':True,
                                                   'original_article_all_fields_preserved':True,'no_http_500':True}
            stage = 'optin_resolved_id_personal_state_contract'
            incoming=article('ntfy-resolve-fixture:1','same-hash')
            resolved=client.post('/api/v1/articles/batch?resolve_ids=true',json={'items':[incoming]})
            assert resolved.status_code==200 and resolved.json()=={'accepted':1,'idMap':{incoming['id']:resolved_id}}, 'ResolvedOriginalIdMissing'
            default=client.post('/api/v1/articles/batch',json={'items':[incoming]})
            assert default.status_code==200 and default.json()=={'accepted':1}, 'DefaultBatchContractChanged'
            patched=client.patch('/api/v1/articles/'+quote(resolved_id,safe='')+'/state',
                                 json={'read':True,'saved':True,'stateUpdatedAt':1234})
            assert patched.status_code==200 and patched.json()['id']==resolved_id and patched.json()['read'] and patched.json()['saved'], 'ResolvedIdStatePatchFailed'
            control={**incoming,'id':'synthetic-resolve-control','tags':['health_status']}
            ignored=client.post('/api/v1/articles/batch?resolve_ids=true',json={'items':[control]})
            assert ignored.status_code==200 and ignored.json()=={'accepted':0,'idMap':{}}, 'ControlMessageResolvedAsArticle'
            with SessionLocal() as db:
                current=db.get(Article,resolved_id)
                assert {c.name:getattr(current,c.name) for c in Article.__table__.columns}==resolved_original, 'ResolvedOriginalChanged'
                assert db.get(Article,incoming['id']) is None and len(list(db.scalars(select(Article))))==2, 'ResolutionCreatedDuplicate'
            result['resolved_id_contract']={'passed':True,'original_id_sha256':digest_bytes(resolved_id.encode()),
                                            'original_all_fields_preserved':True,'mapped_state_patch_succeeded':True,
                                            'default_response_unchanged':True,'control_message_empty_map':True}
            result['mysql_concurrency_executed'] = not args.local_sqlite
        result['passed'] = True
        stage = 'complete'
    except Exception as error:
        result['failure'] = {'stage':stage,'type':type(error).__name__}
        # Only our identifier-shaped assertion/guard codes, never an exception
        # message containing a DSN, SQL parameters, credential or article body.
        if re.fullmatch(r'[A-Z][A-Za-z0-9]{3,90}',str(error)):
            result['failure']['code'] = str(error)
        original_args = getattr(getattr(error,'orig',None),'args',())
        if original_args and isinstance(original_args[0],int):
            result['failure']['database_errno'] = original_args[0]
    finally:
        if child is not None:
            try:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=12)
                    except subprocess.TimeoutExpired:
                        child.kill(); child.wait(timeout=5)
                result['cleanup']['owned_http_process_closed'] = child.poll() is not None
            except Exception as error:
                result['cleanup']['http_error_type'] = type(error).__name__
                result['passed'] = False
        if engine is not None:
            try:
                if owned_tables:
                    from sqlalchemy import inspect
                    existing = set(inspect(engine).get_table_names())
                    unexpected = existing-owned_tables
                    from app.database import Base
                    Base.metadata.drop_all(engine,tables=[Base.metadata.tables[n] for n in sorted(owned_tables)])
                    remaining = set(inspect(engine).get_table_names())
                    result['cleanup']['owned_table_count'] = len(owned_tables)
                    result['cleanup']['owned_tables_remaining'] = len(remaining & owned_tables)
                    result['cleanup']['unexpected_table_count'] = len(unexpected)
                    if remaining or unexpected:
                        result['passed'] = False
                engine.dispose()
                result['cleanup']['engine_disposed'] = True
            except Exception as error:
                result['cleanup']['database_error_type'] = type(error).__name__
                result['passed'] = False
        if work is not None:
            try:
                work.cleanup()
                result['cleanup']['owned_private_workspace_removed'] = True
            except Exception as error:
                result['cleanup']['workspace_error_type'] = type(error).__name__
                result['passed'] = False
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'passed':result['passed'],'actual_http':result['actual_http'],
                          'mysql_concurrency_executed':result['mysql_concurrency_executed'],
                          'checks_passed':sum(bool(v) for v in result['checks'].values()),
                          'failed_stage':result.get('failure',{}).get('stage')},ensure_ascii=False))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    if len(sys.argv)>1 and sys.argv[1] == '--serve':
        parser=argparse.ArgumentParser()
        parser.add_argument('--source-root',type=Path,required=True)
        parser.add_argument('--port',type=int,required=True)
        parser.add_argument('--run-id',required=True)
        parsed=parser.parse_args(sys.argv[2:])
        child_server(parsed.source_root,parsed.port,parsed.run_id)
    else:
        parser=argparse.ArgumentParser(description=__doc__)
        parser.add_argument('--source-root',type=Path,required=True)
        parser.add_argument('--output',type=Path,required=True)
        parser.add_argument('--local-sqlite',action='store_true')
        raise SystemExit(main(parser.parse_args()))
